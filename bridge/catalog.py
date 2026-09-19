def refresh_model_catalog(force: bool = False) -> tuple[dict, int, int]:
    import yaml
    config = yaml.safe_load(PROVIDER_CONFIG_FILE.read_text(encoding="utf-8")) or {}
    providers = config.get("providers") or {}
    try:
        cache = json.loads(MODEL_CACHE_FILE.read_text(encoding="utf-8")) if MODEL_CACHE_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    now = time.time()
    refreshed = 0
    failed = 0
    for provider_id, spec in providers.items():
        if not isinstance(spec, dict):
            continue
        cached = cache.get(provider_id) if isinstance(cache.get(provider_id), dict) else {}
        cached_models = cached.get("models") or []
        if cached_models and not spec.get("models"):
            spec["models"] = cached_models
        if not spec.get("discover_models"):
            continue
        if not force and cached.get("refreshed_at") and now - float(cached["refreshed_at"]) < MODEL_REFRESH_SECONDS:
            if cached_models:
                spec["models"] = cached_models
            continue
        api = str(spec.get("api_endpoint") or spec.get("api") or "").rstrip("/")
        if api.endswith("/chat/completions"):
            api = api[:-len("/chat/completions")]
        if not api:
            failed += 1
            continue
        validate_provider_endpoint(api)
        configured_key_env = spec.get("api_key_env")
        key_env = str(configured_key_env or "LLM_API_KEY")
        key = os.environ.get(key_env, "")
        if not key and (not configured_key_env or key_env == "LLM_API_KEY"):
            key = os.environ.get("LLM_API_KEY", "")
        if configured_key_env and not key:
            failed += 1
            continue
        headers = {"Accept": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "SillyTavernTelegramBridge/1.0"}
        headers.update(spec.get("extra_headers") or {})
        try:
            request = urllib.request.Request(api + "/models", headers=headers, method="GET")
            with strict_urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            models = [str(item.get("id")) for item in (payload.get("data") or []) if isinstance(item, dict) and item.get("id")]
            if models:
                spec["models"] = models
                cache[provider_id] = {"refreshed_at": now, "models": models}
                refreshed += 1
            elif cached_models:
                spec["models"] = cached_models
        except Exception:
            failed += 1
            if cached_models:
                spec["models"] = cached_models
            logging.info("Model discovery failed for provider %s", provider_id, exc_info=True)
    try:
        MODEL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = MODEL_CACHE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(MODEL_CACHE_FILE)
    except OSError:
        logging.warning("Could not write model catalog cache", exc_info=True)
    return config, refreshed, failed


def provider_health_checks(provider_id: str | None = None) -> list[tuple[str, str, str]]:
    import yaml
    config = yaml.safe_load(PROVIDER_CONFIG_FILE.read_text(encoding="utf-8")) or {}
    providers = config.get("providers") or {}
    results = []
    for current_id, spec in providers.items():
        if provider_id and current_id != provider_id:
            continue
        if not isinstance(spec, dict):
            continue
        endpoint = str(spec.get("api_endpoint") or spec.get("api") or "").rstrip("/")
        for suffix in ("/chat/completions", "/messages"):
            if endpoint.endswith(suffix):
                endpoint = endpoint[:-len(suffix)]
        if not endpoint:
            results.append((current_id, str(spec.get("name") or current_id), "not configured"))
            continue
        validate_provider_endpoint(endpoint)
        configured_key_env = spec.get("api_key_env")
        key_env = str(configured_key_env or "LLM_API_KEY")
        key = os.environ.get(key_env, "")
        transport = str(spec.get("transport") or "")
        if not key and (not configured_key_env or key_env == "LLM_API_KEY"):
            key = os.environ.get("LLM_API_KEY", "")
        if configured_key_env and not key and transport != "opencode_muse":
            results.append((current_id, str(spec.get("name") or current_id), f"missing credential ({key_env})"))
            continue
        headers = opencode_muse_headers(f"health:{current_id}") if transport == "opencode_muse" else {"Accept": "application/json", "User-Agent": "SillyTavernTelegramBridge/1.0"}
        if str(spec.get("transport") or "") == "anthropic_messages":
            headers["x-api-key"] = key
            headers["anthropic-version"] = str(spec.get("anthropic_version") or "2023-06-01")
        else:
            headers["Authorization"] = f"Bearer {key}"
        headers.update(spec.get("extra_headers") or {})
        try:
            if str(spec.get("health_check") or "").casefold() == "chat_completion":
                model = str(spec.get("model") or (spec.get("models") or [""])[0])
                health_body = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply OK."}],
                    "max_tokens": 8,
                    "temperature": 0,
                    "stream": True,
                }
                request = urllib.request.Request(endpoint + "/chat/completions", data=json.dumps(health_body).encode("utf-8"), headers={**headers, "Accept": "text/event-stream", "Content-Type": "application/json"}, method="POST")
                with strict_urlopen(request, timeout=30) as response:
                    response.read(1)
                results.append((current_id, str(spec.get("name") or current_id), "healthy (chat completion)"))
                continue
            request = urllib.request.Request(endpoint + "/models", headers=headers, method="GET")
            with strict_urlopen(request, timeout=10) as response:
                results.append((current_id, str(spec.get("name") or current_id), f"healthy ({response.status})"))
        except urllib.error.HTTPError as exc:
            results.append((current_id, str(spec.get("name") or current_id), f"reachable ({exc.code})"))
        except Exception as exc:
            results.append((current_id, str(spec.get("name") or current_id), f"unreachable ({type(exc).__name__})"))
    return results


def get_model_groups() -> dict[str, tuple[str, list[tuple[str, str]], bool]]:
    """Read every bridge provider/model group; mark bridge-supported providers."""
    groups: dict[str, tuple[str, list[tuple[str, str]], bool]] = {}
    supported_adapters = {"chat_completions", "openai", "openai_compatible", "anthropic_messages", "opencode_muse"}
    try:
        config, _, _ = refresh_model_catalog()
        providers = config.get("providers") or {}
        for provider_id, provider in providers.items():
            models = []
            for raw_model in provider.get("models") or []:
                model_id = str(raw_model)
                selection_id = f"{provider_id}::{model_id}"
                models.append((model_id, selection_id))
            unique = []
            seen = set()
            for label, model_id in models:
                if model_id not in seen:
                    seen.add(model_id)
                    unique.append((label, model_id))
            if unique:
                adapter = str(provider.get("adapter") or provider.get("transport") or "")
                groups[provider_id] = (str(provider.get("name") or provider_id), unique[:50], adapter in supported_adapters)
    except Exception:
        logging.warning("Could not read bridge model catalog", exc_info=True)

    if groups:
        return groups

    fallback: dict[str, tuple[str, list[tuple[str, str]], bool]] = {}
    for label, model_id in MODEL_CHOICES:
        provider_id, _actual_model = model_id.split("::", 1)
        fallback.setdefault(provider_id, (provider_id.replace("-", " ").title(), [], True))[1].append((label.split(" · ", 1)[-1], model_id))
    return fallback


def send_model_menu(token: str, chat_id: str, current_model: str, provider_id: str | None = None, message_id: int | None = None, page: int = 0) -> None:
    groups = get_model_groups()
    if provider_id is None:
        options = [(group_id, f"{label} ({len(models)}){'' if is_supported else ' · catalog only'}", models, is_supported) for group_id, (label, models, is_supported) in groups.items()]
        page_options, current_page, total_pages = panel_page(options, page)
        rows = []
        for group_id, label, models, _is_supported in page_options:
            mark = "✅ " if any(model_id == current_model for _, model_id in models) else ""
            rows.append([{"text": mark + panel_label(label), "callback_data": "provider:" + dynamic_callback_token("provider", group_id, chat_id)}])
        if total_pages > 1:
            navigation = []
            if current_page > 0:
                navigation.append({"text": "⬅️ Previous", "callback_data": f"models:providers:{current_page - 1}"})
            if current_page < total_pages - 1:
                navigation.append({"text": "Next ➡️", "callback_data": f"models:providers:{current_page + 1}"})
            rows.append(navigation)
        rows.append([{"text": "🩺 Provider health", "callback_data": "provider:health"}, {"text": "🔄 Refresh models", "callback_data": "provider:refresh"}])
        rows.append([{"text": "❌ Cancel", "callback_data": "models:cancel"}])
        text = f"Current model: {current_model}\nBridge provider catalog (page {current_page + 1}/{total_pages}):"
    else:
        label, models, is_supported = groups.get(provider_id, (provider_id, [], False))
        page_options, current_page, total_pages = panel_page(models, page)
        rows = []
        for model_label, model_id in page_options:
            mark = "✅ " if model_id == current_model else ""
            callback = "model:" + dynamic_callback_token("model", model_id, chat_id) if is_supported else "unsupported:" + dynamic_callback_token("provider", provider_id, chat_id)
            prefix = "" if is_supported else "🚫 "
            rows.append([{"text": prefix + mark + model_label, "callback_data": callback}])
        if total_pages > 1:
            navigation = []
            if current_page > 0:
                navigation.append({"text": "⬅️ Previous", "callback_data": f"models:model:{dynamic_callback_token('provider', provider_id, chat_id)}:{current_page - 1}"})
            if current_page < total_pages - 1:
                navigation.append({"text": "Next ➡️", "callback_data": f"models:model:{dynamic_callback_token('provider', provider_id, chat_id)}:{current_page + 1}"})
            rows.append(navigation)
        rows.append([{"text": "⬅️ Back to providers", "callback_data": "models:back"}])
        rows.append([{"text": "❌ Cancel", "callback_data": "models:cancel"}])
        text = f"Provider: {label}\nCurrent model: {current_model}"
        if total_pages > 1:
            text += f"\nPage {current_page + 1}/{total_pages}"
        if not is_supported:
            text += "\nCatalog visible; this bridge adapter is not enabled yet."
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Panel already shows the requested state")
            return
        raise


def send_model_target_menu(token: str, chat_id: str, current_model: str, utility_model: str, message_id: int | None = None) -> None:
    markup = {"inline_keyboard": [[
        {"text": ("✅ " if current_model else "") + "📖 Story model", "callback_data": "modeltarget:story"},
        {"text": ("✅ " if utility_model else "") + "🛠️ Utility model", "callback_data": "modeltarget:utility"},
    ], [{"text": "❌ Cancel", "callback_data": "models:cancel"}]]}
    send_panel_message(token, chat_id, "Where should the next selected model be used?", markup, message_id)


def send_provider_health_menu(token: str, chat_id: str, message_id: int | None = None) -> None:
    checks = provider_health_checks()
    lines = [f"{name}: {status}" for _provider_id, name, status in checks]
    text = "Provider health\n\n" + ("\n".join(lines) if lines else "No providers configured.")
    markup = {"inline_keyboard": [[{"text": "🔄 Refresh models", "callback_data": "provider:refresh"}], [{"text": "⬅️ Back to providers", "callback_data": "provider:back"}, {"text": "❌ Close", "callback_data": "models:cancel"}]]}
    try:
        send_panel_message(token, chat_id, text, markup, message_id)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Panel already shows the requested state")
            return
        raise


def send_world_menu(token: str, chat_id: str, current_world: str, message_id: int | None = None, page: int = 0) -> None:
    selected = set(active_world_files(current_world))
    options = [(path.name, path.stem) for path in world_file_paths()]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for name, label in page_options:
        callback_token = dynamic_callback_token("world", name, chat_id)
        mark = "✅ " if name in selected else ""
        rows.append([
            {"text": mark + panel_label(label), "callback_data": "world:" + callback_token},
            {"text": "🗑️", "callback_data": "worlddelete:" + callback_token},
        ])
    navigation = panel_navigation("world", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "📤 Upload World JSON", "callback_data": "world:upload"}])
    rows.append([{"text": "🚫 Clear all World Info", "callback_data": "world:off"}, {"text": "✅ Close", "callback_data": "world:done"}])
    selected_label = ", ".join(Path(name).stem for name in selected) if selected else "off"
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Active World Info: {selected_label}{page_label}\nTap lorebooks to toggle them:"
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Panel already shows the requested state")
            return
        raise


def install_world_info_document(filename: str, raw: bytes) -> Path:
    name = Path(str(filename)).name
    if name != str(filename) or Path(name).suffix != ".json" or name in {"", ".", ".."}:
        raise ValueError("World Info upload must be a JSON file with a simple filename")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("World Info JSON is invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        raise ValueError("World Info JSON must contain an entries object")
    WORLD_DIR.mkdir(parents=True, exist_ok=True)
    target = WORLD_DIR / name
    if target.exists():
        raise FileExistsError(f"World Info file already exists: {name}")
    temporary = tempfile.NamedTemporaryFile(prefix=".world-", suffix=".tmp", dir=WORLD_DIR, delete=False)
    try:
        temporary.write(raw)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temporary.name, target)
    except Exception:
        try:
            temporary.close()
        except Exception:
            pass
        Path(temporary.name).unlink(missing_ok=True)
        raise
    return target


def delete_world_info_file(db, chat_id: str, filename: str) -> None:
    path = safe_world_path(filename)
    if path is None:
        raise ValueError("World Info file not found")
    for (world_value,) in db.execute("SELECT world_file FROM sessions").fetchall():
        if path.name in active_world_files(world_value):
            raise ValueError("World Info is active in a session; disable it before deleting")
    path.unlink()


def answer_callback(token: str, callback_id: str, text: str) -> None:
    telegram_request(token, "answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text[:200],
    })
