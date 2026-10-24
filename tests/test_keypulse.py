"""Offline test suite for keypulse. No network required.

Run: python -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from keypulse.core import (  # noqa: E402
    ERROR, INVALID, OK, PLACEHOLDER, RATE_LIMITED,
    check_key, collect_keys, summarize,
)
from keypulse.providers import (  # noqa: E402
    HttpResponse, NetworkError, is_placeholder, mask, parse_env_file,
)


class FakeGet:
    """Configurable fake http_get."""

    def __init__(self, status=200, raise_network=False):
        self.status = status
        self.raise_network = raise_network
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        if self.raise_network:
            raise NetworkError("connection refused")
        return HttpResponse(self.status, '{"data": []}')


# ---------------------------------------------------------------------------
# placeholder detection
# ---------------------------------------------------------------------------

class TestPlaceholderDetection(unittest.TestCase):
    def test_real_keys_pass(self):
        self.assertFalse(is_placeholder("sk-proj-AbCdEf1234567890XyZ"))
        self.assertFalse(is_placeholder("gsk_ABCdefGHIjklMNOpqr"))
        self.assertFalse(is_placeholder("AIzaSyB1234567890abcdefghijklmnopq"))

    def test_template_values_fail(self):
        for bad in ("your-api-key", "YOUR_API_KEY", "changeme", "replace-me",
                    "<your-key>", "xxx", "paste-your-key-here", "sk-xxx-test"):
            self.assertTrue(is_placeholder(bad), bad)

    def test_empty_and_junk(self):
        for bad in ("", "   ", "test", "none", "null", "undefined"):
            self.assertTrue(is_placeholder(bad), repr(bad))

    def test_mask(self):
        self.assertEqual(mask("sk-1234567890abcdef"), "sk-1******cdef")
        self.assertEqual(mask("short"), "*****")
        self.assertEqual(mask(""), "")


# ---------------------------------------------------------------------------
# .env parsing
# ---------------------------------------------------------------------------

class TestEnvParsing(unittest.TestCase):
    def test_parse_env_file(self):
        path = os.path.join(os.path.dirname(__file__), "_fixture.env")
        data = parse_env_file(path)
        self.assertEqual(data["OPENAI_API_KEY"], "sk-real-key-123")
        self.assertEqual(data["GROQ_API_KEY"], "gsk_quoted")
        self.assertEqual(data["ANTHROPIC_API_KEY"], "sk-ant-exported")
        self.assertNotIn("COMMENTED", data)

    def test_missing_file_is_silent(self):
        self.assertEqual(parse_env_file("/nonexistent/.env"), {})


# ---------------------------------------------------------------------------
# key discovery
# ---------------------------------------------------------------------------

class TestCollectKeys(unittest.TestCase):
    def test_first_env_var_wins(self):
        found = collect_keys(env={"GEMINI_API_KEY": "g1", "GOOGLE_API_KEY": "g2"})
        self.assertEqual(found["gemini"], "g1")

    def test_provider_filter(self):
        env = {"OPENAI_API_KEY": "a", "GROQ_API_KEY": "b"}
        found = collect_keys(env=env, providers=["groq"])
        self.assertEqual(found, {"groq": "b"})

    def test_env_files_merged(self):
        path = os.path.join(os.path.dirname(__file__), "_fixture.env")
        env = {"GROQ_API_KEY": "from-env"}
        found = collect_keys(env=env, env_files=[path])
        self.assertEqual(found["openai"], "sk-real-key-123")
        self.assertEqual(found["groq"], "from-env")  # env beats file

    def test_empty_values_ignored(self):
        found = collect_keys(env={"OPENAI_API_KEY": ""})
        self.assertNotIn("openai", found)


# ---------------------------------------------------------------------------
# probe logic (fake http)
# ---------------------------------------------------------------------------

class TestCheckKey(unittest.TestCase):
    def test_ok_200(self):
        fake = FakeGet(status=200)
        r = check_key("openai", "sk-proj-realkey123", http_get=fake)
        self.assertEqual(r.status, OK)
        self.assertEqual(r.http_code, 200)
        self.assertGreaterEqual(r.latency_ms, 0)
        self.assertIn("Bearer", fake.calls[0]["headers"]["Authorization"])

    def test_invalid_401(self):
        r = check_key("openai", "sk-proj-realkey123", http_get=FakeGet(401))
        self.assertEqual(r.status, INVALID)

    def test_invalid_403(self):
        r = check_key("anthropic", "sk-ant-real123", http_get=FakeGet(403))
        self.assertEqual(r.status, INVALID)

    def test_rate_limited_429(self):
        r = check_key("groq", "gsk_real123", http_get=FakeGet(429))
        self.assertEqual(r.status, RATE_LIMITED)

    def test_error_500(self):
        r = check_key("mistral", "real-key-123", http_get=FakeGet(500))
        self.assertEqual(r.status, ERROR)
        self.assertEqual(r.http_code, 500)

    def test_network_error(self):
        r = check_key("openai", "sk-proj-realkey123",
                      http_get=FakeGet(raise_network=True))
        self.assertEqual(r.status, ERROR)
        self.assertIn("network", r.detail)
        self.assertIsNone(r.http_code)

    def test_placeholder_short_circuits(self):
        fake = FakeGet(status=200)
        r = check_key("openai", "your-api-key", http_get=fake)
        self.assertEqual(r.status, PLACEHOLDER)
        self.assertEqual(len(fake.calls), 0)  # no request was made

    def test_empty_key(self):
        r = check_key("openai", "", http_get=FakeGet())
        self.assertEqual(r.status, PLACEHOLDER)

    def test_anthropic_headers(self):
        fake = FakeGet(status=200)
        check_key("anthropic", "sk-ant-real123", http_get=fake)
        h = fake.calls[0]["headers"]
        self.assertEqual(h["x-api-key"], "sk-ant-real123")
        self.assertEqual(h["anthropic-version"], "2023-06-01")

    def test_gemini_key_in_url(self):
        fake = FakeGet(status=200)
        check_key("gemini", "AIzaSyAbCd123", http_get=fake)
        self.assertIn("key=AIzaSyAbCd123", fake.calls[0]["url"])
        self.assertNotIn("Authorization", fake.calls[0]["headers"])

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            check_key("nonexistent", "abc", http_get=FakeGet())

    def test_result_dict_shape(self):
        r = check_key("openai", "sk-proj-realkey123", http_get=FakeGet(200))
        d = r.to_dict()
        self.assertEqual(d["provider"], "openai")
        self.assertEqual(d["status"], OK)
        self.assertIn("*", d["key"])  # masked, never raw


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

class TestSummarize(unittest.TestCase):
    def _result(self, status):
        r = check_key("openai", "sk-proj-realkey123", http_get=FakeGet(200))
        r.status = status
        return r

    def test_all_ok(self):
        s = summarize([self._result(OK), self._result(OK)])
        self.assertTrue(s["all_ok"])
        self.assertEqual(s["ok"], 2)

    def test_mixed(self):
        s = summarize([self._result(OK), self._result(INVALID),
                       self._result(RATE_LIMITED), self._result(PLACEHOLDER),
                       self._result(ERROR)])
        self.assertFalse(s["all_ok"])
        self.assertEqual(s["total"], 5)
        self.assertEqual(s["rate_limited"], 1)

    def test_empty(self):
        self.assertEqual(summarize([])["total"], 0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli(unittest.TestCase):
    def _main(self, *argv, env=None):
        from keypulse.cli import main
        import unittest.mock
        with unittest.mock.patch.dict(os.environ, env or {}, clear=True):
            return main(list(argv))

    def test_list_providers(self):
        code = self._main("--list-providers")
        self.assertEqual(code, 0)

    def test_json_output_and_exit_codes(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            # valid key vs fake http? -- this path hits the real network.
            # instead drive CLI via injected checks: use --provider/--key
            # with a placeholder so no request happens.
            code = self._main("--provider", "openai", "--key", "changeme",
                              "--json")
        out = json.loads(buf.getvalue())
        self.assertEqual(code, 64)  # nothing healthy -> all-dead exit code
        self.assertEqual(out["summary"]["placeholder"], 1)
        self.assertEqual(out["results"][0]["status"], PLACEHOLDER)

    def test_no_keys_found(self):
        code = self._main("--json")
        self.assertEqual(code, 1)

    def test_key_provider_pairing_error(self):
        with self.assertRaises(SystemExit):
            self._main("--provider", "openai", "--key", "a", "--key", "b")

    def test_min_ok_gate_pass(self):
        # placeholder keys never count as ok -> gate fails
        code = self._main("--provider", "openai", "--key", "placeholder",
                         "--min-ok", "1")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
