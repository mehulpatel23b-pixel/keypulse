"""Core probe logic: run one key through its provider check.

Kept completely I/O-injectable so tests run offline: pass a fake
``http_get`` and no network is touched.
"""

import time
import urllib.parse

from .providers import (
    PROVIDERS,
    HttpResponse,
    is_placeholder,
    mask,
    parse_env_file,
    default_http_get,
    NetworkError,
)

# status taxonomy
OK = "ok"                  # provider accepted the key
INVALID = "invalid"        # 401/403: key rejected
RATE_LIMITED = "rate-limited"  # 429: key valid but throttled / quota spent
PLACEHOLDER = "placeholder"    # value never was a real key
ERROR = "error"            # network/timeout/unknown -- verdict unknown

_HTTP_MAP = {401: INVALID, 403: INVALID, 429: RATE_LIMITED}


class KeyResult:
    """Outcome of one provider probe."""

    def __init__(self, provider, status, http_code=None, latency_ms=None,
                 detail="", masked_key=""):
        self.provider = provider
        self.status = status
        self.http_code = http_code
        self.latency_ms = latency_ms
        self.detail = detail
        self.masked_key = masked_key

    def to_dict(self):
        return {
            "provider": self.provider,
            "status": self.status,
            "http_code": self.http_code,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "key": self.masked_key,
        }


def _build_url(provider_cfg, key):
    url = provider_cfg["url"]
    if provider_cfg.get("key_in_url"):
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode({"key": key})
    return url


def check_key(provider, key, timeout=10.0, http_get=None):
    """Probe a single provider key. Returns a KeyResult.

    ``http_get(url, headers, timeout) -> HttpResponse`` is injectable;
    default performs a real network request.
    """
    if provider not in PROVIDERS:
        raise ValueError("unknown provider: %r" % provider)
    cfg = PROVIDERS[provider]
    masked = mask(key)
    key = (key or "").strip()

    if not key:
        return KeyResult(provider, PLACEHOLDER, detail="empty key", masked_key=masked)
    if is_placeholder(key):
        return KeyResult(provider, PLACEHOLDER,
                         detail="value looks like a template/example, not a real key",
                         masked_key=masked)

    url = _build_url(cfg, key)
    headers = dict(cfg["headers"](key))
    getter = http_get or default_http_get

    start = time.monotonic()
    try:
        resp = getter(url, headers, timeout)
    except NetworkError as err:
        return KeyResult(provider, ERROR, detail="network: %s" % err,
                         masked_key=masked)
    latency_ms = int((time.monotonic() - start) * 1000)

    if not isinstance(resp, HttpResponse):
        return KeyResult(provider, ERROR, detail="bad response object",
                         masked_key=masked)

    status = _HTTP_MAP.get(resp.status, OK if 200 <= resp.status < 300 else ERROR)
    detail = {INVALID: "provider rejected the key",
              RATE_LIMITED: "key valid but throttled or out of quota",
              ERROR: "unexpected HTTP status"}.get(status, "key accepted")
    return KeyResult(provider, status, http_code=resp.status,
                      latency_ms=latency_ms, detail=detail, masked_key=masked)


def collect_keys(env=None, env_files=(".env",), providers=None):
    """Discover keys from environment (and .env files) per provider.

    Returns {provider_name: key_string} -- first env var that exists wins.
    """
    merged = {}
    for path in env_files:
        merged.update(parse_env_file(path))
    merged.update(env or {})  # real environment beats .env files
    out = {}
    for name in (providers or PROVIDERS):
        for var in PROVIDERS[name]["env"]:
            value = merged.get(var)
            if value:
                out[name] = value
                break
    return out


def summarize(results):
    """Aggregate a list of KeyResult into a summary dict."""
    total = len(results)
    by_status = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    ok_count = by_status.get(OK, 0)
    return {
        "total": total,
        "ok": ok_count,
        "invalid": by_status.get(INVALID, 0),
        "rate_limited": by_status.get(RATE_LIMITED, 0),
        "placeholder": by_status.get(PLACEHOLDER, 0),
        "error": by_status.get(ERROR, 0),
        "all_ok": total > 0 and ok_count == total,
    }
