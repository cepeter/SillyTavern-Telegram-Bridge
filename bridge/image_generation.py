"""Opt-in OpenAI-compatible image generation for Telegram."""

IMAGE_PROMPT_MAX_CHARS = 4000
IMAGE_DEFAULT_SIZE = "1024x1024"
IMAGE_RESPONSE_FORMAT = "b64_json"
IMAGE_CONTENT_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _image_provider_specs() -> list[tuple[str, dict, str]]:
    import yaml
    if not PROVIDER_CONFIG_FILE.exists():
        return []
    config = yaml.safe_load(PROVIDER_CONFIG_FILE.read_text(encoding="utf-8")) or {}
    result = []
    for provider_id, raw in (config.get("providers") or {}).items():
        if not isinstance(raw, dict) or not raw.get("image_enabled"):
            continue
        models = [str(item) for item in raw.get("image_models") or [] if str(item)]
        if models:
            result.append((str(provider_id), raw, models[0]))
    return result


def _resolve_image_provider(selection: str = "") -> tuple[str, dict, str]:
    requested_provider, requested_model = (selection.split("::", 1) + [""])[:2] if "::" in selection else ("", selection)
    for provider_id, spec, default_model in _image_provider_specs():
        models = [str(item) for item in spec.get("image_models") or []]
        if requested_provider and provider_id != requested_provider:
            continue
        model = requested_model or default_model
        if model in models:
            return provider_id, spec, model
    raise ValueError("No image provider is enabled; configure image_enabled and image_models in the private provider catalog")


def _image_endpoint(spec: dict) -> str:
    base = str(spec.get("image_endpoint") or spec.get("api_endpoint") or spec.get("api") or "").rstrip("/")
    if not base:
        raise ValueError("Image provider endpoint is missing")
    if not spec.get("image_endpoint"):
        base += "/images/generations"
    validate_provider_endpoint(base.rsplit("/images/generations", 1)[0])
    return base


def _image_headers(spec: dict) -> dict[str, str]:
    key_env = str(spec.get("api_key_env") or "LLM_API_KEY")
    key = os.environ.get(key_env, "") or os.environ.get("LLM_API_KEY", "")
    if spec.get("api_key_env") and not key:
        raise ValueError(f"Image provider credential is missing ({key_env})")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "SillyTavernTelegramBridge/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(spec.get("extra_headers") or {})
    return headers


def _image_bytes_from_response(payload: dict, spec: dict) -> tuple[bytes, str]:
    items = payload.get("data") if isinstance(payload, dict) else None
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    encoded = item.get("b64_json")
    if encoded:
        try:
            raw = base64.b64decode(str(encoded), validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("Image provider returned invalid base64") from exc
        return raw, str(item.get("revised_prompt") or "")
    url = str(item.get("url") or "")
    if not url:
        raise ValueError("Image provider returned neither b64_json nor url")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Image URL must use HTTPS")
    validate_provider_endpoint(f"{parsed.scheme}://{parsed.netloc}")
    with strict_urlopen(urllib.request.Request(url, headers={"Accept": "image/*"}), timeout=120) as response:
        raw = response.read(IMAGE_MAX_BYTES + 1)
    return raw, str(item.get("revised_prompt") or "")


def generate_image(selection: str, prompt: str, size: str = IMAGE_DEFAULT_SIZE) -> tuple[bytes, str, str]:
    prompt = " ".join(str(prompt or "").split())
    if not 1 <= len(prompt) <= IMAGE_PROMPT_MAX_CHARS:
        raise ValueError(f"Image prompt must contain 1–{IMAGE_PROMPT_MAX_CHARS} characters")
    if not re.fullmatch(r"(?:256|512|1024|1536)x(?:256|512|1024|1536)", size):
        raise ValueError("Image size must use WIDTHxHEIGHT with supported dimensions")
    provider_id, spec, model = _resolve_image_provider(selection)
    endpoint = _image_endpoint(spec)
    body = {"model": model, "prompt": prompt, "size": size, "n": 1, "response_format": IMAGE_RESPONSE_FORMAT}
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=_image_headers(spec), method="POST")
    with strict_urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raw, revised = _image_bytes_from_response(payload, spec)
    if not raw or len(raw) > IMAGE_MAX_BYTES:
        raise ValueError("Generated image is empty or exceeds the Telegram image limit")
    return raw, revised, f"{provider_id}::{model}"


def _multipart_photo(token: str, chat_id: str, raw: bytes, caption: str) -> None:
    real_chat_id, thread_id = parse_topic_scope(chat_id)
    boundary = f"----BridgeImagine{int(time.time() * 1000000)}"
    fields = [("chat_id", real_chat_id)]
    if thread_id is not None:
        fields.append(("message_thread_id", str(thread_id)))
    if caption:
        fields.append(("caption", caption[:1024]))
    chunks = []
    for name, value in fields:
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"imagine.png\"\r\nContent-Type: image/png\r\n\r\n".encode() + raw + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    response = json.loads(urllib.request.urlopen(request, timeout=60).read().decode("utf-8"))  # nosec B310 - fixed HTTPS Telegram endpoint
    if not response.get("ok"):
        raise RuntimeError("Telegram rejected generated image delivery")


def handle_imagine_prompt(token: str, chat_id: str, prompt: str, selection: str = "", size: str = IMAGE_DEFAULT_SIZE) -> None:
    send_text(token, chat_id, "🎨 Generating image…")
    raw, revised, used = generate_image(selection, prompt, size)
    caption = f"🎨 {prompt[:700]}\nModel: {used}"
    if revised and revised != prompt:
        caption += f"\nRevised: {revised[:250]}"
    _multipart_photo(token, chat_id, raw, caption)
