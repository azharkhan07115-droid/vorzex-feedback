"""Wake the VORZEX feedback web service without sending feedback data."""

import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def health_url(raw_url: str) -> str:
    value = raw_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("VORZEX_KEEPALIVE_URL must be a public HTTPS URL")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("VORZEX_KEEPALIVE_URL must not contain credentials, query or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/feedback"):
        path = path[: -len("/feedback")]
    if not path.endswith("/health"):
        path += "/health"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def ping(url: str) -> int:
    request = urllib.request.Request(
        health_url(url),
        headers={"User-Agent": "VORZEX-Render-Keepalive/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return 0 if 200 <= response.status < 300 else 1
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 1


if __name__ == "__main__":
    endpoint = os.environ.get("VORZEX_KEEPALIVE_URL", "")
    if not endpoint:
        print("VORZEX_KEEPALIVE_URL is not configured", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(ping(endpoint))
