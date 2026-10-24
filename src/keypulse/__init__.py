#!/usr/bin/env python3
"""keypulse -- heartbeat check for your LLM API keys.

Zero-dependency CLI that verifies API keys across LLM providers
(OpenAI, Anthropic, Google Gemini, Groq, Mistral, OpenRouter,
DeepSeek, Together, Cerebras) by pinging each provider's cheapest
endpoint (the models list / key-info endpoint). Reports status,
HTTP code and latency -- and NEVER prints key material.

Stdlib only. Python 3.9+.
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
