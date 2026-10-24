# keypulse

**Heartbeat check for your LLM API keys.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-000?style=flat-square&logo=python&logoColor=3776AB)](https://www.python.org)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-000?style=flat-square&logo=gnome&logoColor=4CAF50)](requirements.txt)
[![License: MIT](https://img.shields.io/badge/license-MIT-000?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-30%2F30-000?style=flat-square&logo=checkmarx&logoColor=4CAF50)](tests/)

One command tells you which of your AI provider keys are **alive, dead, throttled or were never real in the first place**. If you build AI agents, you probably juggle keys for OpenAI, Anthropic, Gemini, Groq, Mistral, OpenRouter, DeepSeek, Together and Cerebras — and the first time you learn one expired is when your agent dies in production at 2am. keypulse pings each provider's **cheapest endpoint** (the models list / key-info endpoint: no tokens billed) and reports the verdict, HTTP status and latency. It **never prints key material** — only masked previews.

```text
$ keypulse
openai      ok            http=200     142 ms  sk-p******9f2a  key accepted
anthropic   INVALID       http=401     131 ms  sk-a******WXYZ  provider rejected the key
gemini      rate-limited  http=429     208 ms  AIza******cdef  key valid but throttled or out of quota
groq        placeholder   http=-          -   gsk_******      value looks like a template/example, not a real key
----------------------------------------------------------------------------------------
total=4 ok=1 invalid=1 rate-limited=1 placeholder=1 error=0
```

## Why

- **Dead keys break agents silently.** Providers revoke keys, orgs rotate them, quotas run out — your stack fails at request time, not at deploy time.
- **`.env` files rot.** `your-api-key` templates get committed, keys get swapped in dashboards, and nothing verifies the new value actually works until the first real call.
- **Existing checks are manual.** People curl each provider by hand with different header conventions (`Authorization: Bearer`, `x-api-key` + version header, `?key=` query param). keypulse knows each provider's dialect so you don't have to.
- **Key hygiene is a 2026 boardroom topic**: teams audit key rotation, sharing and validity — keypulse is the free, local, dependency-free layer of that stack.

## Installation

```bash
# from source (recommended)
git clone https://github.com/mehulpatel23b-pixel/keypulse.git
cd keypulse
pip install .

# or run without installing anything
PYTHONPATH=src python -m keypulse --list-providers
```

No third-party packages are required — the entire tool is Python 3.9+ standard library (`urllib`, `json`, `argparse`, `time`). See [requirements.txt](requirements.txt).

## Quickstart

```bash
# 1. Check every key it can discover from your environment + ./.env
keypulse

# 2. Check a specific key (explicit pairing, e.g. fetched from a vault in CI)
keypulse --provider openai --key "$OPENAI_KEY"

# 3. Machine-readable output for scripts
keypulse --json

# 4. CI gate: fail the build unless at least 3 keys are healthy
keypulse --min-ok 3

# 5. Extra env files
keypulse --env-file .env --env-file .env.local

# 6. What can keypulse check?
keypulse --list-providers
```

### Key discovery

keypulse looks for the first non-empty of these environment variables (real environment always beats `.env` files):

| Provider | Environment variables | Probe endpoint |
|---|---|---|
| openai | `OPENAI_API_KEY` | `GET api.openai.com/v1/models` |
| anthropic | `ANTHROPIC_API_KEY` | `GET api.anthropic.com/v1/models` (x-api-key + version header) |
| gemini | `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_AI_STUDIO_KEY` | `GET generativelanguage.googleapis.com/v1beta/models?key=` |
| groq | `GROQ_API_KEY` | `GET api.groq.com/openai/v1/models` |
| mistral | `MISTRAL_API_KEY` | `GET api.mistral.ai/v1/models` |
| openrouter | `OPENROUTER_API_KEY` | `GET openrouter.ai/api/v1/key` (key-info endpoint) |
| deepseek | `DEEPSEEK_API_KEY` | `GET api.deepseek.com/models` |
| together | `TOGETHER_API_KEY` | `GET api.together.xyz/v1/models` |
| cerebras | `CEREBRAS_API_KEY` | `GET api.cerebras.ai/v1/models` |

All probes are **reads**: listing models (or fetching key info for OpenRouter). Nothing is generated, no tokens are billed.

## Verdicts

| Status | Meaning | Trigger |
|---|---|---|
| `ok` | Provider accepted the key | HTTP 2xx on the probe |
| `invalid` | Key was rejected | HTTP 401/403 |
| `rate-limited` | Key is valid but throttled or out of quota | HTTP 429 |
| `placeholder` | Value never was a real key | `your-api-key`, `changeme`, `xxx`, `sk-proj-xxx`, empty, etc. — no request is even made |
| `error` | Verdict unknown | network failure, timeout, unexpected HTTP status |

## Exit codes (CI gate)

| Code | Meaning |
|---|---|
| `0` | Every probed key is healthy |
| `1` | At least one key invalid / error, or nothing to probe, or `--min-ok` gate failed |
| `2` | Bad CLI usage |
| `64` | Nothing healthy at all (all dead or all placeholders) |

`--min-ok N` flips the exit code to `1` unless at least `N` keys are `ok` — pair it with a scheduled CI job and you get an alert the moment a key dies, not the moment a user complains.

## Architecture

```mermaid
flowchart LR
    A[CLI args] --> B{key source}
    B -- --provider/--key --> C[Explicit pairs]
    B -- discovery --> D[env vars + .env parser]
    C --> E[placeholder guard]
    D --> E
    E -- real-looking key --> F[Provider adapter\nURL + auth dialect]
    E -- template/empty --> G[Result: placeholder\nno request made]
    F --> H[HTTP GET\ninjectable for tests]
    H --> I[Status classifier\n200/401/403/429/other]
    I --> J[Masked KeyResult\nstatus, latency, http code]
    J --> K[Text or JSON report\n+ exit-code policy]
```

```
src/keypulse/
├── providers.py   # provider registry: endpoints, auth dialects, env names;
│                  # .env parser; placeholder heuristics; key masking
├── core.py        # check_key() probe engine (I/O-injectable), discovery,
│                  # result objects, summary aggregation
├── cli.py         # argparse UI, text/JSON rendering, exit-code policy
├── __init__.py
└── __main__.py    # python -m keypulse

tests/
├── test_keypulse.py  # 30 offline tests: no network needed
└── _fixture.env      # fixture .env (obviously fake values)
```

Design notes:

- **I/O injection**: `check_key(provider, key, http_get=...)` accepts any callable with the `(url, headers, timeout) -> HttpResponse` signature. The entire test suite runs offline against a fake transport — and you can plug in a proxy or logging transport without touching the core.
- **Provider dialects are data, not code**: adding a provider is one registry entry (endpoint + header builder + env names). The Anthropic `x-api-key` + `anthropic-version` header and the Gemini `?key=` query param are lambda entries in [providers.py](src/keypulse/providers.py).
- **Placeholder guard short-circuits**: template values never hit the network, so a `.env` full of stubs costs zero requests and is reported honestly as `placeholder`.
- **No key material in output**: results carry only a masked preview (`sk-p******9f2a`); JSON output likewise. Verbose error bodies from providers are summarized to a fixed detail string.

## Sample output

Text mode (masked keys, latency per probe):

```text
$ keypulse --provider openai --key sk-ant-0123456789abcdefWXYZ
openai      INVALID       http=401    138 ms  sk-p******WXYZ  provider rejected the key
----------------------------------------------------------------------------------------
total=1 ok=0 invalid=1 rate-limited=0 placeholder=0 error=0
```

JSON mode (stable schema for scripts):

```json
{
  "summary": {"total": 1, "ok": 0, "invalid": 1, "rate_limited": 0,
              "placeholder": 0, "error": 0, "all_ok": false},
  "results": [
    {"provider": "anthropic", "status": "invalid", "http_code": 401,
     "latency_ms": 125, "detail": "provider rejected the key",
     "key": "sk-a******WXYZ"}
  ]
}
```

## Real-world use cases

1. **Post-rotation smoke test** — rotated keys in your vault? Run `keypulse` before you deploy; catch the copy-paste typo while it's still cheap.
2. **CI health gate** — a scheduled workflow runs `keypulse --min-ok 3` and fails the build when a provider revokes a key or an org quota runs dry.
3. **`.env` rot detector** — onboard a repo, run `keypulse`, and instantly see which entries are stale placeholders that were never filled in.
4. **Multi-provider agent failover** — before a long batch run, probe all providers and route around anything that answers 401/429 today.
5. **Onboarding / handover audits** — new team member inherited six dashboards' worth of keys? One command shows which are alive and which are folklore.
6. **Incident triage** — "the bot is down" becomes a 3-second diagnosis: `keypulse --json` shows whether it's auth (401), quota (429) or network (error).
7. **Cost-aware latency checks** — the latency column doubles as a free liveness check of each provider API from your environment, at zero token cost.

## Security & honesty

- keypulse is a **read-only** liveness probe. It never sends your key anywhere except the official provider endpoint for that key.
- It never prints or logs raw keys; `.env` files are in `.gitignore` and the bundled fixture contains only obviously fake values.
- **Honest limits**: `ok` means "the endpoint accepted this key for a models-list request". It does not guarantee your org's billing is current for large generation calls, that the key has scopes beyond model listing, or that per-model access is enabled. A 429 may mean throttling or exhausted quota — keypulse reports the fact, not the provider's billing internals.
- Placeholder heuristics are intentionally aggressive; if your key legitimately contains a fragment like `test` or `example`, it will be flagged as a placeholder rather than probed. That's the safe direction to be wrong in.

## Testing

```bash
python -m unittest discover -s tests -v   # 30/30, fully offline
```

## License

[MIT](LICENSE) © 2026 mehulpatel23b-pixel
