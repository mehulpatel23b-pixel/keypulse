"""Provider registry: endpoints, auth headers and env var names.

Each provider entry describes how to perform the cheapest possible
authenticated request. Most providers expose a "list models" endpoint
that is free (no tokens billed) but requires a valid key; OpenRouter
exposes a dedicated key-info endpoint instead.
"""

import json
import urllib.request

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROVIDERS = {
    "openai": {
        "env": ["OPENAI_API_KEY"],
        "url": "https://api.openai.com/v1/models",
        "headers": lambda key: {"Authorization": "Bearer " + key},
    },
    "anthropic": {
        "env": ["ANTHROPIC_API_KEY"],
        "url": "https://api.anthropic.com/v1/models",
        "headers": lambda key: {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    },
    "gemini": {
        # query-param auth, header callback returns {} and key goes in URL
        "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_AI_STUDIO_KEY"],
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "headers": lambda key: {},
        "key_in_url": True,
    },
    "groq": {
        "env": ["GROQ_API_KEY"],
        "url": "https://api.groq.com/openai/v1/models",
        "headers": lambda key: {"Authorization": "Bearer " + key},
    },
    "mistral": {
        "env": ["MISTRAL_API_KEY"],
        "url": "https://api.mistral.ai/v1/models",
        "headers": lambda key: {"Authorization": "Bearer " + key},
    },
    "openrouter": {
        "env": ["OPENROUTER_API_KEY"],
        "url": "https://openrouter.ai/api/v1/key",
        "headers": lambda key: {"Authorization": "Bearer " + key},
    },
    "deepseek": {
        "env": ["DEEPSEEK_API_KEY"],
        "url": "https://api.deepseek.com/models",
        "headers": lambda key: {"Authorization": "Bearer " + key},
    },
    "together": {
        "env": ["TOGETHER_API_KEY"],
        "url": "https://api.together.xyz/v1/models",
        "headers": lambda key: {"Authorization": "Bearer " + key},
    },
    "cerebras": {
        "env": ["CEREBRAS_API_KEY"],
        "url": "https://api.cerebras.ai/v1/models",
        "headers": lambda key: {"Authorization": "Bearer " + key},
    },
}

# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------

PLACEHOLDER_FRAGMENTS = (
    "your-api-key", "your_api_key", "yourapikey", "changeme", "change-me",
    "replace-me", "example", "dummy", "placeholder", "test_key", "testkey",
    "insert", "paste", "xxx", "****", "sk-proj-xxx", "<", "fake",
    "not-a-real-key", "todo",
)


def is_placeholder(key):
    """True if the value looks like a template/example rather than a real key."""
    lowered = (key or "").strip().lower()
    if not lowered:
        return True
    if lowered in ("test", "none", "null", "undefined", "secret", "key"):
        return True
    return any(frag in lowered for frag in PLACEHOLDER_FRAGMENTS)


def mask(key):
    """Safely display a key: first 4 and last 4 characters only."""
    key = key or ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * 6 + key[-4:]


# ---------------------------------------------------------------------------
# .env parsing (no dependency on python-dotenv)
# ---------------------------------------------------------------------------

def parse_env_file(path):
    """Parse a .env-style file into a dict. Ignores comments/blanks."""
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:]
                if "=" not in line:
                    continue
                name, _, value = line.partition("=")
                value = value.strip().strip("'\"")
                out[name.strip()] = value
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------------
# HTTP probe
# ---------------------------------------------------------------------------

class HttpResponse:
    """Minimal response object returned by the injectable http_get."""

    def __init__(self, status, body=""):
        self.status = status
        self.body = body


class NetworkError(Exception):
    """Raised when the request could not complete at all (DNS, timeout...)."""


def default_http_get(url, headers, timeout):
    """Real network call via urllib. Returns HttpResponse even for 4xx/5xx.

    Raises NetworkError only when no HTTP response was received at all
    (DNS failure, refused connection, timeout).
    """
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(resp.status, resp.read(4096).decode("utf-8", "replace"))
    except urllib.error.HTTPError as err:
        # provider answered: this is still a valid health-check datapoint
        try:
            body = err.read(4096).decode("utf-8", "replace")
        except Exception:
            body = ""
        return HttpResponse(err.code, body)
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise NetworkError(str(err))
