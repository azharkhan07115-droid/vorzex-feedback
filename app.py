import hashlib
import hmac
import io
import json
import os
import re
import struct
import threading
import time
from collections import defaultdict, deque

import requests
from flask import Flask, jsonify, request


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
APP_SECRET = os.environ.get("VORZEX_FEEDBACK_APP_SECRET", "").encode("utf-8")

MAX_CLOCK_SKEW_SECONDS = 90
MAX_CAPTION_CHARS = 720
MAX_IMAGE_PIXELS = 8_500_000
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{20,96}$")

CUSTOM_IDS = (
    "6221996895535896347",  # rocket
    "5319201848022808495",  # sparkle
    "6088957586302831521",  # camera
    "6221820660142838830",  # arrow
    "6224390807227470978",  # check
    "5316635510574169206",  # top
    "5316553695742147732",  # camera alternate
    "5190806721286657692",  # chart
)
CUSTOM_FALLBACKS = ("🚀", "✨", "📸", "➡️", "✅", "🔝", "📷", "📊")

STYLE_LABELS = {
    "Status": "𝗦𝗧𝗔𝗧𝗨𝗦",
    "Capture": "𝗖𝗔𝗣𝗧𝗨𝗥𝗘",
    "Device": "𝗗𝗘𝗩𝗜𝗖𝗘",
    "Android": "𝗔𝗡𝗗𝗥𝗢𝗜𝗗",
    "VORZEX": "𝗩𝗢𝗥𝗭𝗘𝗫",
    "Date": "𝗗𝗔𝗧𝗘",
    "Mode": "𝗠𝗢𝗗𝗘",
    "Result": "𝗥𝗘𝗦𝗨𝗟𝗧",
    "Win margin": "𝗪𝗜𝗡 𝗠𝗔𝗥𝗚𝗜𝗡",
    "Match shots": "𝗠𝗔𝗧𝗖𝗛 𝗦𝗛𝗢𝗧𝗦",
    "Match duration": "𝗠𝗔𝗧𝗖𝗛 𝗧𝗜𝗠𝗘",
    "Engine total shots": "𝗧𝗢𝗧𝗔𝗟 𝗦𝗛𝗢𝗧𝗦",
    "Engine accuracy": "𝗔𝗖𝗖𝗨𝗥𝗔𝗖𝗬",
    "Verified wins": "𝗩𝗘𝗥𝗜𝗙𝗜𝗘𝗗 𝗪𝗜𝗡𝗦",
}

_lock = threading.Lock()
_seen_nonces: dict[str, float] = {}
_rate: dict[str, deque[float]] = defaultdict(deque)


def _configured() -> bool:
    return (
        bool(re.fullmatch(r"[0-9]{6,}:[A-Za-z0-9_-]{20,}", BOT_TOKEN))
        and bool(re.fullmatch(r"(?:-?[0-9]{5,}|@[A-Za-z0-9_]{4,})", CHAT_ID))
        and len(APP_SECRET) >= 32
    )


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width < 100 or height < 100 or width * height > MAX_IMAGE_PIXELS:
        return None
    return width, height


def _expected_signature(
    version: str, timestamp: str, nonce: str, image: bytes, caption: str, kind: str
) -> str:
    image_hash = hashlib.sha256(image).hexdigest()
    payload = f"{version}\n{timestamp}\n{nonce}\n{kind}\n{image_hash}\n{caption}".encode("utf-8")
    return hmac.new(APP_SECRET, payload, hashlib.sha256).hexdigest()


def _claim_nonce(nonce: str, client_key: str, now: float) -> bool:
    with _lock:
        expired = [key for key, deadline in _seen_nonces.items() if deadline < now]
        for key in expired:
            _seen_nonces.pop(key, None)
        if nonce in _seen_nonces:
            return False
        bucket = _rate[client_key]
        while bucket and bucket[0] < now - 3600:
            bucket.popleft()
        if len(bucket) >= 30:
            return False
        bucket.append(now)
        _seen_nonces[nonce] = now + MAX_CLOCK_SKEW_SECONDS * 2
        return True


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _styled_caption(plain: str, kind: str) -> tuple[str, list[dict[str, object]]]:
    source = plain.splitlines() or ["VORZEX ENGINE"]
    start = 0 if kind == "launch" else 4
    title = "𝗩𝗢𝗥𝗭𝗘𝗫 𝗘𝗡𝗚𝗜𝗡𝗘  •  " + ("𝗟𝗔𝗨𝗡𝗖𝗛" if kind == "launch" else "𝗩𝗜𝗖𝗧𝗢𝗥𝗬")
    content = [title, "━━━━━━━━━━━━━━━━━━━━"]
    for line in source[1:]:
        label, separator, value = line.partition(":")
        if separator:
            content.append(f"{STYLE_LABELS.get(label.strip(), label.strip().upper())}  ›  {value.strip()}")
        elif line.strip():
            content.append(line.strip())

    rendered: list[str] = []
    entities: list[dict[str, object]] = []
    offset = 0
    for index, line in enumerate(content):
        slot = (start + index) % len(CUSTOM_IDS)
        emoji = CUSTOM_FALLBACKS[slot]
        rendered_line = f"{emoji}  {line}"
        entities.append({
            "type": "custom_emoji",
            "offset": offset,
            "length": _utf16_length(emoji),
            "custom_emoji_id": CUSTOM_IDS[slot],
        })
        rendered.append(rendered_line)
        offset += _utf16_length(rendered_line) + 1
    return "\n".join(rendered), entities


def _telegram_send(image: bytes, caption: str, kind: str, animated: bool) -> requests.Response:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    styled, entities = _styled_caption(caption, kind)
    data = {
        "chat_id": CHAT_ID,
        "caption": styled,
        "protect_content": "true",
    }
    if animated:
        data["caption_entities"] = json.dumps(entities, separators=(",", ":"))
    filename = "vorzex-launch.png" if kind == "launch" else "vorzex-win.png"
    return requests.post(
        url,
        data=data,
        files={"photo": (filename, io.BytesIO(image), "image/png")},
        timeout=(15, 30),
    )


@app.get("/health")
def health():
    return jsonify(ok=True, configured=_configured()), 200 if _configured() else 503


@app.post("/feedback")
def feedback():
    if not _configured():
        return jsonify(ok=False, error="server_not_configured"), 503

    timestamp = request.headers.get("X-Vorzex-Timestamp", "")
    nonce = request.headers.get("X-Vorzex-Nonce", "")
    signature = request.headers.get("X-Vorzex-Signature", "").lower()
    version = request.headers.get("X-Vorzex-Version", "")
    kind = request.form.get("kind", "").lower()
    caption = request.form.get("caption", "")
    upload = request.files.get("photo")

    if version != "18" or kind not in {"launch", "win"} or not NONCE_RE.fullmatch(nonce):
        return jsonify(ok=False, error="invalid_request"), 400
    try:
        sent_at = int(timestamp)
    except ValueError:
        return jsonify(ok=False, error="invalid_timestamp"), 400
    now = int(time.time())
    if abs(now - sent_at) > MAX_CLOCK_SKEW_SECONDS:
        return jsonify(ok=False, error="expired_request"), 401
    if not caption or len(caption) > MAX_CAPTION_CHARS or upload is None:
        return jsonify(ok=False, error="invalid_payload"), 400

    image = upload.read(10 * 1024 * 1024 + 1)
    if len(image) > 10 * 1024 * 1024 or _png_dimensions(image) is None:
        return jsonify(ok=False, error="invalid_png"), 400
    expected = _expected_signature(version, timestamp, nonce, image, caption, kind)
    if not re.fullmatch(r"[0-9a-f]{64}", signature) or not hmac.compare_digest(expected, signature):
        return jsonify(ok=False, error="unauthorized"), 401

    forwarded = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    client_key = hashlib.sha256(forwarded.split(",", 1)[0].strip().encode()).hexdigest()[:20]
    if not _claim_nonce(nonce, client_key, float(now)):
        return jsonify(ok=False, error="replay_or_rate_limit"), 429

    try:
        response = _telegram_send(image, caption, kind, animated=True)
        premium_emoji = response.status_code == 200 and response.json().get("ok", False)
        if response.status_code != 200 or not response.json().get("ok", False):
            # Channels require Fragment additional-username eligibility for bot custom emoji.
            # Preserve the unique Unicode typography while retrying without custom entities.
            response = _telegram_send(image, caption, kind, animated=False)
            premium_emoji = False
        if response.status_code == 200 and response.json().get("ok", False):
            return jsonify(ok=True, premium_emoji=premium_emoji), 200
        return jsonify(ok=False, error="telegram_rejected"), 502
    except (requests.RequestException, ValueError):
        return jsonify(ok=False, error="telegram_unavailable"), 502


@app.errorhandler(413)
def too_large(_error):
    return jsonify(ok=False, error="payload_too_large"), 413

