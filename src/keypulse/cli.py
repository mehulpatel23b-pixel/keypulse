"""Command-line interface for keypulse."""

import argparse
import json
import sys

from .core import (
    ERROR, INVALID, OK, PLACEHOLDER, RATE_LIMITED,
    check_key, collect_keys, summarize,
)
from .providers import PROVIDERS, mask

# exit codes (documented in README)
EXIT_OK = 0        # every probed key is healthy
EXIT_FAIL = 1      # at least one key invalid / error
EXIT_USAGE = 2      # bad CLI usage
EXIT_ALL_DEAD = 64  # nothing healthy at all

_STATUS_ICON = {
    OK: "ok",
    RATE_LIMITED: "rate-limited",
    INVALID: "INVALID",
    PLACEHOLDER: "placeholder",
    ERROR: "ERROR",
}


def build_parser():
    p = argparse.ArgumentParser(
        prog="keypulse",
        description="Heartbeat check for your LLM API keys. "
                    "Pings each provider's cheapest endpoint and reports "
                    "which keys are alive. Never prints key material.")
    p.add_argument("--provider", action="append", metavar="NAME",
                   help="probe only these providers (repeatable); "
                        "default: every provider whose key can be discovered")
    p.add_argument("--key", action="append", metavar="KEY",
                   help="explicit key(s); paired with --provider in order "
                        "(useful in CI where keys come from a vault)")
    p.add_argument("--env-file", action="append", default=None, metavar="PATH",
                   help="extra .env-style file(s) to read keys from "
                        "(default: ./.env if present)")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="per-request timeout in seconds (default: 10)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="machine-readable JSON output")
    p.add_argument("--min-ok", type=int, default=None, metavar="N",
                   help="exit non-zero unless at least N keys are healthy "
                        "(CI gate)")
    p.add_argument("--list-providers", action="store_true",
                   help="print supported providers and exit")
    return p


def _discover_env_files(args):
    if args.env_file:
        return args.env_file
    return [".env"]


def run_checks(args):
    """Resolve what to probe and run it. Returns list of KeyResult."""
    explicit = args.provider or []
    if args.key:
        if not explicit or len(args.key) != len(explicit):
            raise SystemExit(
                "--key must be paired 1:1 with --provider "
                "(e.g. --provider openai --key sk-...)")
        return [check_key(name, key, timeout=args.timeout)
                for name, key in zip(explicit, args.key)]

    # discovery mode: env vars + .env files
    found = collect_keys(env=dict(_iter_env()), env_files=_discover_env_files(args),
                         providers=explicit or None)
    if not found:
        return []
    return [check_key(name, key, timeout=args.timeout)
            for name, key in sorted(found.items())]


def _iter_env():
    import os
    return {k: v for k, v in os.environ.items() if v and k.endswith("_API_KEY")}


def render_text(results, summary):
    if not results:
        return ("no keys found -- set provider env vars "
                "(e.g. OPENAI_API_KEY) or pass --provider/--key")
    lines = []
    for r in results:
        icon = _STATUS_ICON.get(r.status, "?")
        latency = "%5d ms" % r.latency_ms if r.latency_ms is not None else "     -"
        code = str(r.http_code) if r.http_code is not None else "-"
        lines.append("%-11s %-13s http=%-4s %s  %-12s %s"
                     % (r.provider, icon, code, latency, r.masked_key, r.detail))
    lines.append("-" * 72)
    lines.append("total=%d ok=%d invalid=%d rate-limited=%d placeholder=%d error=%d"
                 % (summary["total"], summary["ok"], summary["invalid"],
                    summary["rate_limited"], summary["placeholder"],
                    summary["error"]))
    return "\n".join(lines)


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.list_providers:
        for name in sorted(PROVIDERS):
            print("%-11s env: %s" % (name, ", ".join(PROVIDERS[name]["env"])))
        return EXIT_OK

    results = run_checks(args)
    summary = summarize(results)

    if args.as_json:
        print(json.dumps({
            "summary": summary,
            "results": [r.to_dict() for r in results],
        }, indent=2))
    else:
        print(render_text(results, summary))

    if not results:
        return EXIT_FAIL

    # CI gates
    if args.min_ok is not None and summary["ok"] < args.min_ok:
        return EXIT_FAIL
    if summary["all_ok"]:
        return EXIT_OK
    if summary["ok"] == 0:
        return EXIT_ALL_DEAD
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
