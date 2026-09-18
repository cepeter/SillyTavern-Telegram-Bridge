import subprocess
import sys

from bridge.rag_retrieval import (
    DEFAULT_SEMANTIC_CANDIDATE_LIMIT,
    MAX_SEMANTIC_CANDIDATE_LIMIT,
    cosine_similarity,
    semantic_candidate_chunk_ids,
)


def extract_pdf_data_bank_text(raw: bytes) -> str:
    parser = Path(__file__).with_name("pdf_parser.py")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(parser),
                "--max-bytes",
                str(RAG_MAX_FILE_BYTES),
                "--max-pages",
                str(RAG_MAX_PDF_PAGES),
                "--max-chars",
                str(RAG_MAX_EXTRACTED_CHARS),
            ],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=RAG_PDF_PARSE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PDF parsing timed out") from exc
    except OSError as exc:
        raise ValueError("PDF parser worker is unavailable") from exc
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PDF parser returned invalid output") from exc
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "invalid or unreadable PDF file"))
    return str(result.get("text") or "")


def extract_data_bank_text(filename: str, raw: bytes) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix not in RAG_SUPPORTED_SUFFIXES:
        raise ValueError("unsupported Data Bank format")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 50 * 1024 * 1024 or (info.compress_size and info.file_size / info.compress_size > 1000):
                    raise ValueError("DOCX XML member is too large or highly compressed")
                xml = archive.read(info)
            root = ET.fromstring(xml)
            text = "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("invalid DOCX file") from exc
    elif suffix == ".pdf":
        text = extract_pdf_data_bank_text(raw)
    else:
        text = raw.decode("utf-8", errors="replace")
        if suffix in {".html", ".htm", ".xml"}:
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
    if len(normalized) > RAG_MAX_EXTRACTED_CHARS:
        raise ValueError(f"extracted document text exceeds {RAG_MAX_EXTRACTED_CHARS} characters")
    return normalized


def split_data_bank_chunks(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + RAG_CHUNK_CHARS)
        if end < len(text):
            boundary = text.rfind("\n", start + RAG_CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - RAG_CHUNK_OVERLAP)
    return chunks


def rag_embedding_namespace() -> str:
    revision = os.environ.get("SILLYTAVERN_RAG_EMBEDDING_REVISION", "1")
    identity = f"{RAG_EMBEDDING_URL}|{RAG_EMBEDDING_MODEL}|{RAG_EMBEDDING_DIMENSIONS}|{revision}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def rag_embedding_headers() -> dict[str, str]:
    parsed = urllib.parse.urlparse(RAG_EMBEDDING_URL)
    host = (parsed.hostname or "").casefold()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    validate_provider_endpoint(RAG_EMBEDDING_URL, "SILLYTAVERN_RAG_ALLOWED_HOSTS")
    key = os.environ.get("SILLYTAVERN_RAG_EMBEDDING_API_KEY", "")
    if not key and not loopback:
        raise RuntimeError("dedicated SILLYTAVERN_RAG_EMBEDDING_API_KEY is required for external embedding endpoints")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def embed_rag_text(text: str) -> list[float] | None:
    try:
        request = urllib.request.Request(RAG_EMBEDDING_URL, data=json.dumps({"model": RAG_EMBEDDING_MODEL, "input": text[:6000]}).encode("utf-8"), headers=rag_embedding_headers(), method="POST")
        with strict_urlopen(request, timeout=60, allowed_env="SILLYTAVERN_RAG_ALLOWED_HOSTS") as response:
            result = json.loads(response.read().decode("utf-8"))
        vector = (result.get("data") or [{}])[0].get("embedding") or []
        if len(vector) != RAG_EMBEDDING_DIMENSIONS:
            logging.warning("Unexpected RAG embedding dimensions: %s", len(vector))
            return None
        return [float(value) for value in vector]
    except Exception:
        logging.warning("RAG embedding unavailable; using lexical search", exc_info=True)
        return None


def embed_rag_batch(texts: list[str]) -> list[list[float] | None]:
    if not texts:
        return []
    try:
        request = urllib.request.Request(RAG_EMBEDDING_URL, data=json.dumps({"model": RAG_EMBEDDING_MODEL, "input": [text[:6000] for text in texts]}).encode("utf-8"), headers=rag_embedding_headers(), method="POST")
        with strict_urlopen(request, timeout=120, allowed_env="SILLYTAVERN_RAG_ALLOWED_HOSTS") as response:
            result = json.loads(response.read().decode("utf-8"))
        vectors = [None] * len(texts)
        for item in result.get("data") or []:
            index = int(item.get("index", 0))
            vector = item.get("embedding") or []
            if 0 <= index < len(vectors) and len(vector) == RAG_EMBEDDING_DIMENSIONS:
                vectors[index] = [float(value) for value in vector]
        return vectors
    except Exception:
        logging.warning("Batch RAG embedding unavailable; falling back to single requests", exc_info=True)
        return [embed_rag_text(text) for text in texts]


def rag_semantic_candidate_limit() -> int:
    raw = os.environ.get("SILLYTAVERN_RAG_SEMANTIC_CANDIDATES", str(DEFAULT_SEMANTIC_CANDIDATE_LIMIT))
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_SEMANTIC_CANDIDATE_LIMIT
    return max(64, min(value, MAX_SEMANTIC_CANDIDATE_LIMIT))


def add_data_bank_document(db: sqlite3.Connection, chat_id: str, filename: str, raw: bytes) -> tuple[str, int]:
    if len(raw) > RAG_MAX_FILE_BYTES:
        raise ValueError("Data Bank file exceeds 10 MB")
    text = extract_data_bank_text(filename, raw)
    if not text:
        raise ValueError("Data Bank file contains no readable text")
    document_id = hashlib.sha256(raw).hexdigest()
    existing = db.execute("SELECT chunk_count FROM data_bank_documents WHERE chat_id=? AND document_id=?", (chat_id, document_id)).fetchone()
    if existing:
        return "duplicate", int(existing[0])
    chunks = split_data_bank_chunks(text)
    namespace = rag_embedding_namespace()
    cache_keys = [namespace + ":content:" + hashlib.sha256(content.encode("utf-8")).hexdigest() for content in chunks]
    vector_cache = {}
    for offset in range(0, len(chunks), 32):
        batch_keys = cache_keys[offset:offset + 32]
        placeholders = ",".join("?" for _ in batch_keys)
        for cache_key, vector_json in db.execute(f"SELECT cache_key,vector_json FROM rag_embedding_cache WHERE cache_key IN ({placeholders})", batch_keys).fetchall():
            try:
                vector_cache[cache_key] = json.loads(vector_json)
            except json.JSONDecodeError:
                continue
        missing = [(index, chunks[index]) for index, cache_key in enumerate(cache_keys[offset:offset + 32], start=offset) if cache_key not in vector_cache]
        for batch_start in range(0, len(missing), 32):
            selected = missing[batch_start:batch_start + 32]
            vectors = embed_rag_batch([item[1] for item in selected])
            for (index, _), vector in zip(selected, vectors):
                if vector:
                    cache_key = cache_keys[index]
                    vector_cache[cache_key] = vector
                    db.execute("INSERT OR REPLACE INTO rag_embedding_cache(cache_key,dimensions,vector_json,created_at) VALUES(?,?,?,?)", (cache_key, len(vector), json.dumps(vector, separators=(",", ":")), time.time()))
    now = time.time()
    db.execute("INSERT INTO data_bank_documents(chat_id,document_id,filename,byte_size,chunk_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (chat_id, document_id, filename[:255], len(raw), len(chunks), now, now))
    for index, content in enumerate(chunks):
        cursor = db.execute("INSERT INTO data_bank_chunks(chat_id,document_id,chunk_index,content) VALUES(?,?,?,?)", (chat_id, document_id, index, content))
        chunk_id = cursor.lastrowid
        db.execute("INSERT INTO data_bank_fts(content,chat_id,document_id,filename,chunk_id) VALUES(?,?,?,?,?)", (content, chat_id, document_id, filename[:255], chunk_id))
        vector = vector_cache.get(cache_keys[index])
        if vector:
            db.execute("INSERT INTO data_bank_embeddings(chunk_id,embedding_namespace,dimensions,vector_json) VALUES(?,?,?,?)", (chunk_id, namespace, len(vector), json.dumps(vector, separators=(",", ":"))))
    db.commit()
    return "added", len(chunks)


def cached_rag_embedding(db: sqlite3.Connection, text: str) -> list[float] | None:
    cache_key = rag_embedding_namespace() + ":query:" + hashlib.sha256(text[:6000].encode("utf-8")).hexdigest()
    row = db.execute("SELECT vector_json FROM rag_embedding_cache WHERE cache_key=?", (cache_key,)).fetchone()
    if row:
        try:
            vector = json.loads(row[0])
            if len(vector) == RAG_EMBEDDING_DIMENSIONS:
                return [float(value) for value in vector]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    vector = embed_rag_text(text)
    if vector:
        db.execute("INSERT OR REPLACE INTO rag_embedding_cache(cache_key,dimensions,vector_json,created_at) VALUES(?,?,?,?)", (cache_key, len(vector), json.dumps(vector, separators=(",", ":")), time.time()))
        db.commit()
    return vector


def retrieve_data_bank(db: sqlite3.Connection, chat_id: str, query: str, limit: int = 5) -> list[tuple[str, str, str]]:
    terms = re.findall(r"[^\W_]{2,}", query.casefold(), flags=re.UNICODE)[:12]
    if not terms:
        return []
    if not db.execute("SELECT 1 FROM data_bank_documents WHERE chat_id=? LIMIT 1", (chat_id,)).fetchone():
        return []
    candidates = {}
    match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    lexical_rows = db.execute("SELECT CAST(f.chunk_id AS INTEGER),f.filename,c.content,c.document_id,bm25(data_bank_fts) FROM data_bank_fts f JOIN data_bank_chunks c ON c.chunk_id=CAST(f.chunk_id AS INTEGER) WHERE f.chat_id=? AND data_bank_fts MATCH ? ORDER BY bm25(data_bank_fts) LIMIT 50", (chat_id, match)).fetchall()
    for rank, (chunk_id, filename, content, document_id, _score) in enumerate(lexical_rows):
        lexical_score = 1.0 / (1.0 + rank)
        candidates[int(chunk_id)] = [str(filename), str(content), str(document_id), 0.35 * lexical_score]
    query_vector = cached_rag_embedding(db, query)
    if query_vector:
        namespace = rag_embedding_namespace()
        semantic_ids = semantic_candidate_chunk_ids(
            db,
            chat_id,
            namespace,
            [int(row[0]) for row in lexical_rows],
            candidate_limit=rag_semantic_candidate_limit(),
        )
        if semantic_ids:
            placeholders = ",".join("?" for _ in semantic_ids)
            vector_rows = db.execute(
                "SELECT e.chunk_id,c.content,d.filename,c.document_id,e.vector_json "
                "FROM data_bank_embeddings e "
                "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
                "JOIN data_bank_documents d ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
                f"WHERE c.chat_id=? AND e.embedding_namespace=? AND e.chunk_id IN ({placeholders})",
                (chat_id, namespace, *semantic_ids),
            ).fetchall()
        else:
            vector_rows = []
        for chunk_id, content, filename, document_id, vector_json in vector_rows:
            try:
                semantic_score = (cosine_similarity(query_vector, json.loads(vector_json)) + 1.0) / 2.0
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            item = candidates.setdefault(int(chunk_id), [str(filename), str(content), str(document_id), 0.0])
            item[3] += 0.65 * semantic_score
    ranked = sorted(candidates.values(), key=lambda item: item[3], reverse=True)
    return [(item[0], item[1], item[2]) for item in ranked[:limit]]


def rag_mode(db: sqlite3.Connection, chat_id: str) -> str:
    return get_meta(db, f"rag_mode:{chat_id}", "on")


def rag_retrieval_bundle(db: sqlite3.Connection, chat_id: str, query: str, limit: int = 5) -> dict[str, object]:
    if rag_mode(db, chat_id) != "on":
        return {"results": [], "context": "", "sources": []}
    results = retrieve_data_bank(db, chat_id, query, limit=limit)
    context_parts = []
    sources = []
    remaining = RAG_MAX_CONTEXT_CHARS
    for filename, content, _document_id in results:
        piece = f"[{filename}]\n{content}"
        if remaining <= 0:
            break
        included = piece[:remaining]
        if included:
            context_parts.append(included)
            if filename not in sources:
                sources.append(filename)
            remaining -= len(included)
    context = "\n\n".join(context_parts)
    return {"results": results, "context": context, "sources": sources}


def rag_context_for_prompt(db: sqlite3.Connection, chat_id: str, query: str, bundle: dict[str, object] | None = None) -> str:
    return str((bundle or rag_retrieval_bundle(db, chat_id, query)).get("context") or "")


def rag_citation_footer(db: sqlite3.Connection, chat_id: str, query: str, bundle: dict[str, object] | None = None) -> str:
    sources = list((bundle or rag_retrieval_bundle(db, chat_id, query)).get("sources") or [])
    return "\n\nSources: " + ", ".join(f"[{name}]" for name in sources) if sources else ""


def data_bank_documents(db: sqlite3.Connection, chat_id: str) -> list[tuple[str, str, int, int]]:
    return db.execute("SELECT document_id,filename,byte_size,chunk_count FROM data_bank_documents WHERE chat_id=? ORDER BY updated_at DESC", (chat_id,)).fetchall()


def delete_data_bank_documents(db: sqlite3.Connection, chat_id: str, filename: str) -> int:
    documents = db.execute("SELECT document_id FROM data_bank_documents WHERE chat_id=? AND filename=?", (chat_id, filename)).fetchall()
    for (document_id,) in documents:
        db.execute("DELETE FROM data_bank_fts WHERE chat_id=? AND document_id=?", (chat_id, document_id))
        db.execute("DELETE FROM data_bank_embeddings WHERE chunk_id IN (SELECT chunk_id FROM data_bank_chunks WHERE chat_id=? AND document_id=?)", (chat_id, document_id))
        db.execute("DELETE FROM data_bank_chunks WHERE chat_id=? AND document_id=?", (chat_id, document_id))
        db.execute("DELETE FROM data_bank_documents WHERE chat_id=? AND document_id=?", (chat_id, document_id))
    db.commit()
    return len(documents)


def rag_embedding_coverage(db: sqlite3.Connection, chat_id: str) -> tuple[int, int]:
    total = int(db.execute("SELECT COALESCE(SUM(chunk_count),0) FROM data_bank_documents WHERE chat_id=?", (chat_id,)).fetchone()[0])
    indexed = int(db.execute("SELECT COUNT(*) FROM data_bank_embeddings e JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id WHERE c.chat_id=? AND e.embedding_namespace=?", (chat_id, rag_embedding_namespace())).fetchone()[0])
    return total, indexed


def reindex_data_bank_documents(db: sqlite3.Connection, chat_id: str, filename: str | None = None) -> tuple[int, int]:
    namespace = rag_embedding_namespace()
    params = [chat_id]
    query = "SELECT document_id,filename FROM data_bank_documents WHERE chat_id=?"
    if filename:
        query += " AND filename=?"
        params.append(filename)
    documents = db.execute(query, params).fetchall()
    total = indexed = 0
    for document_id, _name in documents:
        rows = db.execute("SELECT chunk_id,content FROM data_bank_chunks WHERE chat_id=? AND document_id=? ORDER BY chunk_index", (chat_id, document_id)).fetchall()
        missing = []
        for chunk_id, content in rows:
            total += 1
            exists = db.execute("SELECT 1 FROM data_bank_embeddings WHERE chunk_id=? AND embedding_namespace=?", (chunk_id, namespace)).fetchone()
            if not exists:
                missing.append((int(chunk_id), str(content)))
        for offset in range(0, len(missing), 32):
            batch = missing[offset:offset + 32]
            vectors = embed_rag_batch([content for _, content in batch])
            for (chunk_id, _content), vector in zip(batch, vectors):
                if vector:
                    db.execute("INSERT OR REPLACE INTO data_bank_embeddings(chunk_id,embedding_namespace,dimensions,vector_json) VALUES(?,?,?,?)", (chunk_id, namespace, len(vector), json.dumps(vector, separators=(",", ":"))))
                    indexed += 1
    db.commit()
    return total, indexed


def handle_data_bank_command(db: sqlite3.Connection, token: str, chat_id: str, command_text: str) -> None:
    parts = command_text.split(None, 3)
    argument = parts[1].casefold() if len(parts) > 1 else "status"
    if argument in {"on", "off"}:
        set_meta(db, f"rag_mode:{chat_id}", argument)
    if argument in {"status", "on", "off"}:
        docs = data_bank_documents(db, chat_id)
        send_text(token, chat_id, f"Data Bank RAG: {rag_mode(db, chat_id)}\nDocuments: {len(docs)}\nChunks: {sum(int(row[3]) for row in docs)}")
        return
    if argument == "reindex":
        filename = parts[2].strip() if len(parts) > 2 else None
        total, indexed = reindex_data_bank_documents(db, chat_id, filename)
        send_text(token, chat_id, f"Data Bank reindex complete: {indexed}/{total} chunks indexed for the current embedding namespace.")
        return
    if argument == "remove":
        filename = parts[2].strip() if len(parts) > 2 else ""
        confirmed = len(parts) > 3 and parts[3].casefold() == "confirm"
        if not filename:
            send_text(token, chat_id, "Use /databank remove <filename> confirm.")
        elif not confirmed:
            send_text(token, chat_id, f"This deletes every Data Bank copy named {filename}. Repeat: /databank remove {filename} confirm")
        else:
            removed = delete_data_bank_documents(db, chat_id, filename)
            send_text(token, chat_id, f"Removed {removed} Data Bank document(s) named {filename}.")
        return
    if argument == "list":
        docs = data_bank_documents(db, chat_id)
        lines = [f"- {row[1]} ({row[3]} chunks)" for row in docs[:20]]
        send_text(token, chat_id, "Data Bank documents:\n" + ("\n".join(lines) if lines else "No documents uploaded."))
        return
    if argument == "search":
        query = parts[2].strip() if len(parts) > 2 else ""
        results = retrieve_data_bank(db, chat_id, query)
        text = "\n\n".join(f"[{filename}]\n{content}" for filename, content, _ in results)
        send_text(token, chat_id, "Data Bank search:\n" + (text[:MAX_TELEGRAM_LENGTH] if text else "No matching chunks found."))
        return
    send_text(token, chat_id, "Use /databank on, /databank off, /databank list, /databank search <query>, or /databank remove <filename> confirm.")
