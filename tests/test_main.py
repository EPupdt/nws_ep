import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from news_hub import main as news_main


class JsonResponse(io.BytesIO):
    def __init__(self, payload, headers=None, status=200):
        super().__init__(payload)
        self.headers = headers
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class IncompleteJsonResponse:
    def __init__(self, partial):
        self.partial = partial

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def read(self, *args, **kwargs):
        raise IncompleteRead(self.partial)


class LlmSelectionTests(unittest.TestCase):
    policy = {
        "max_europe_now": 2,
        "top_story_count": 5,
        "models": {"gemini": "gemini-3.8-flash", "openrouter": ["openrouter/free"]},
    }
    articles = [{"id": "article-1", "title": "Example", "excerpt": "Example", "url": "https://example.com"}]

    def test_null_gemini_content_falls_back_to_openrouter(self):
        selection = {
            "europe_now": [],
            "top_stories": [{
                "title": "Example story",
                "summary": "First sentence. Second sentence.",
                "article_ids": ["article-1"],
            }],
        }
        responses = iter([
            {"candidates": [{"content": {"parts": [{"text": None}]}}]},
            {"choices": [{"message": {"content": json.dumps(selection)}}]},
        ])

        def fake_urlopen(request, timeout):
            return JsonResponse(json.dumps(next(responses)).encode())

        diagnostics = io.StringIO()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-key", "OR_API_KEY": "openrouter-test-key"}, clear=True):
            with patch.object(news_main, "urlopen", side_effect=fake_urlopen), redirect_stderr(diagnostics):
                selected, model = news_main.llm_selection(self.articles, self.policy, [])

        self.assertEqual(selection, selected)
        self.assertEqual("openrouter:openrouter/free", model)
        self.assertIn("empty-or-non-text response", diagnostics.getvalue())
        self.assertNotIn("gemini-test-key", diagnostics.getvalue())
        self.assertNotIn("openrouter-test-key", diagnostics.getvalue())

    def test_null_content_without_fallback_returns_failed(self):
        response = {"candidates": [{"content": {"parts": [{"text": None}]}}]}
        diagnostics = io.StringIO()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-key"}, clear=True):
            with patch.object(news_main, "urlopen", return_value=JsonResponse(json.dumps(response).encode())):
                with redirect_stderr(diagnostics):
                    selected, model = news_main.llm_selection(self.articles, self.policy, [])

        self.assertEqual({"europe_now": [], "top_stories": []}, selected)
        self.assertEqual("failed", model)

    def test_selection_rejects_non_list_article_ids(self):
        selection = {
            "europe_now": [],
            "top_stories": [{"title": "Example", "summary": "Summary", "article_ids": "article-1"}],
        }
        self.assertFalse(news_main.valid_selection(selection, 5, 2))

    def test_complete_json_is_recovered_from_incomplete_http_chunk(self):
        selection = {
            "europe_now": [],
            "top_stories": [{
                "title": "Example story",
                "summary": "First sentence. Second sentence.",
                "article_ids": ["article-1"],
            }],
        }
        response = {"candidates": [{"content": {"parts": [{"text": json.dumps(selection)}]}}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-key"}, clear=True):
            with patch.object(news_main, "urlopen", return_value=IncompleteJsonResponse(json.dumps(response).encode())):
                selected, model = news_main.llm_selection(self.articles, self.policy, [])

        self.assertEqual(selection, selected)
        self.assertEqual("gemini:gemini-3.8-flash", model)

    def test_truncated_json_falls_back_without_crashing(self):
        selection = {
            "europe_now": [],
            "top_stories": [{
                "title": "Example story",
                "summary": "First sentence. Second sentence.",
                "article_ids": ["article-1"],
            }],
        }
        responses = iter([
            IncompleteJsonResponse(b'{"candidates": ['),
            JsonResponse(json.dumps({"choices": [{"message": {"content": json.dumps(selection)}}]}).encode()),
        ])

        diagnostics = io.StringIO()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-key", "OR_API_KEY": "openrouter-test-key"}, clear=True):
            with patch.object(news_main, "urlopen", side_effect=lambda request, timeout: next(responses)):
                with redirect_stderr(diagnostics):
                    selected, model = news_main.llm_selection(self.articles, self.policy, [])

        self.assertEqual(selection, selected)
        self.assertEqual("openrouter:openrouter/free", model)
        self.assertIn("IncompleteRead", diagnostics.getvalue())

    def test_http_error_detail_redacts_key(self):
        key = "gemini-test-key"
        error = HTTPError(
            url="https://generativelanguage.googleapis.com/v1beta/models/test:generateContent",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(f'{{"error":{{"message":"request failed for {key}?key={key}"}}}}'.encode()),
        )
        detail = news_main.safe_llm_error_detail(error, key)
        self.assertNotIn(key, detail)
        self.assertIn("[REDACTED]", detail)


class LlmRetryTests(unittest.TestCase):
    policy = LlmSelectionTests.policy
    articles = LlmSelectionTests.articles
    selection = {"europe_now": [], "top_stories": [
        {"title": "Germany election", "summary": "First sentence. Second sentence.", "article_ids": ["article-1"]}]}
    keys = {"GEMINI_API_KEY": "gemini-test-key", "OR_API_KEY": "openrouter-test-key"}

    def response(self, provider="gemini", selection=None, actual_model=None):
        text = json.dumps(self.selection if selection is None else selection)
        payload = ({"candidates": [{"content": {"parts": [{"text": text}]}}], "modelVersion": actual_model}
                   if provider == "gemini" else
                   {"choices": [{"message": {"content": text}}], "model": actual_model})
        return JsonResponse(json.dumps(payload).encode())

    def error(self, status, headers=None, payload=None):
        return HTTPError("https://example.com/api", status, "Synthetic failure", headers,
                         io.BytesIO(json.dumps(payload or {"error": {"code": status}}).encode()))

    def select(self, responses, keys=None, policy=None):
        audit = []
        diagnostics = io.StringIO()
        with patch.dict(os.environ, self.keys if keys is None else keys, clear=True):
            with patch.object(news_main, "urlopen", side_effect=responses) as request:
                with patch.object(news_main.time, "sleep") as sleep, patch.object(news_main.random, "uniform", return_value=0):
                    with redirect_stderr(diagnostics):
                        selected, model = news_main.llm_selection(self.articles, policy or self.policy, [], audit)
        return selected, model, audit, request.call_args_list, sleep.call_args_list, diagnostics.getvalue()

    def test_transient_errors_retry_same_provider_then_succeed(self):
        for provider in ("gemini", "openrouter"):
            for status in (429, 503):
                with self.subTest(provider=provider, status=status):
                    keys = self.keys if provider == "gemini" else {"OR_API_KEY": self.keys["OR_API_KEY"]}
                    selected, model, audit, calls, sleeps, _ = self.select(
                        [self.error(status), self.response(provider)], keys)
                    attempts = [record for record in audit if "attempt" in record]
                    self.assertEqual(self.selection, selected)
                    self.assertTrue(model.startswith(provider + ":"))
                    self.assertEqual(2, len(calls))
                    self.assertEqual(calls[0].args[0].full_url, calls[1].args[0].full_url)
                    self.assertEqual([1], [call.args[0] for call in sleeps])
                    self.assertEqual(status, attempts[0]["error_code"])
                    self.assertEqual("success", attempts[1]["outcome"])

    def test_exhausted_retries_fall_back_and_are_bounded(self):
        selected, model, audit, calls, sleeps, _ = self.select(
            [self.error(503), self.error(503), self.error(503), self.response("openrouter")])
        self.assertEqual(self.selection, selected)
        self.assertEqual("openrouter:openrouter/free", model)
        self.assertEqual(4, len(calls))
        self.assertEqual([1, 2], [call.args[0] for call in sleeps])
        self.assertEqual([1, 2, 3, 1], [record["attempt"] for record in audit])

    def test_new_gemini_unavailable_uses_previous_gemini_before_openrouter(self):
        policy = {**self.policy, "models": {
            **self.policy["models"], "gemini_fallbacks": ["gemini-3.5-flash-lite"]}}
        for status in (404, 429, 503):
            with self.subTest(status=status):
                count = 1 if status == 404 else 3
                selected, model, audit, calls, sleeps, _ = self.select(
                    [*[self.error(status) for _ in range(count)],
                     self.response(actual_model="gemini-3.5-flash-lite")], policy=policy)
                self.assertEqual(self.selection, selected)
                self.assertEqual("gemini:gemini-3.5-flash-lite", model)
                self.assertEqual(count + 1, len(calls))
                self.assertTrue(all("gemini-3.8-flash:generateContent" in call.args[0].full_url
                                    for call in calls[:-1]))
                self.assertIn("gemini-3.5-flash-lite:generateContent", calls[-1].args[0].full_url)
                self.assertEqual("gemini-3.5-flash-lite", audit[-1]["actual_model"])
                self.assertEqual("success", audit[-1]["outcome"])

    def test_openrouter_still_follows_both_gemini_models(self):
        policy = {**self.policy, "models": {
            **self.policy["models"], "gemini_fallbacks": ["gemini-3.5-flash-lite"]}}
        selected, model, audit, calls, sleeps, _ = self.select(
            [self.error(404), self.error(404), self.response("openrouter")], policy=policy)
        self.assertEqual(self.selection, selected)
        self.assertEqual("openrouter:openrouter/free", model)
        self.assertEqual(["gemini-3.8-flash", "gemini-3.5-flash-lite", "openrouter/free"],
                         [record["requested_model"] for record in audit])
        self.assertEqual([], sleeps)

    def test_both_providers_exhaust_retries_and_return_failed(self):
        selected, model, audit, calls, sleeps, _ = self.select([self.error(429) for _ in range(6)])
        self.assertEqual("failed", model)
        self.assertEqual({"europe_now": [], "top_stories": []}, selected)
        self.assertEqual(6, len(calls))
        self.assertEqual(4, len(sleeps))

    def test_permanent_errors_do_not_retry(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                _, model, audit, calls, sleeps, _ = self.select([self.error(status), self.response("openrouter")])
                self.assertEqual("openrouter:openrouter/free", model)
                self.assertEqual(2, len(calls))
                self.assertEqual([], sleeps)
                self.assertEqual(status, audit[0]["http_status"])

    def test_retry_after_seconds_is_respected(self):
        _, model, audit, _, sleeps, _ = self.select([self.error(429, {"Retry-After": "7"}), self.response()])
        self.assertEqual("gemini:gemini-3.8-flash", model)
        self.assertEqual([7], [call.args[0] for call in sleeps])
        self.assertEqual(7, audit[0]["retry_delay_seconds"])

    def test_retry_after_http_date_and_invalid_headers(self):
        with patch.object(news_main, "utcnow", return_value=datetime(2026, 10, 5, 12, 0, tzinfo=UTC)):
            self.assertEqual(12, news_main.retry_after_seconds("Mon, 05 Oct 2026 12:00:12 GMT"))
            self.assertEqual(0, news_main.retry_after_seconds("Mon, 05 Oct 2026 11:59:59 GMT"))
        for value in (None, "invalid", "NaN", "inf"):
            self.assertIsNone(news_main.retry_after_seconds(value))

    def test_long_retry_after_uses_fallback_without_shortening_wait(self):
        _, model, audit, calls, sleeps, _ = self.select([
            self.error(429, {"Retry-After": "61"}), self.response("openrouter")])
        self.assertEqual("openrouter:openrouter/free", model)
        self.assertEqual(2, len(calls))
        self.assertEqual([], sleeps)
        self.assertNotIn("retry_delay_seconds", audit[0])

    def test_cumulative_wait_budget_is_respected(self):
        _, model, audit, calls, sleeps, _ = self.select([
            self.error(503, {"Retry-After": "40"}), self.error(503, {"Retry-After": "40"}),
            self.response("openrouter")])
        self.assertEqual("openrouter:openrouter/free", model)
        self.assertEqual(3, len(calls))
        self.assertEqual([40], [call.args[0] for call in sleeps])

    def test_daily_quota_goes_directly_to_fallback(self):
        payload = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
            {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
        _, model, audit, calls, sleeps, _ = self.select([self.error(429, payload=payload), self.response("openrouter")])
        self.assertEqual("openrouter:openrouter/free", model)
        self.assertEqual(2, len(calls))
        self.assertEqual([], sleeps)
        self.assertTrue(audit[0]["daily_quota"])

    def test_http_200_embedded_error_retries_and_records_real_model(self):
        payload = {"error": {"code": 503, "message": "Synthetic overload", "metadata": {"error_type": "provider_overloaded"}}}
        _, model, audit, calls, sleeps, _ = self.select([
            JsonResponse(json.dumps(payload).encode(), {"Retry-After": "3"}),
            self.response("openrouter", actual_model="example/free-model:free")],
            {"OR_API_KEY": self.keys["OR_API_KEY"]})
        attempts = [record for record in audit if "attempt" in record]
        self.assertEqual("openrouter:openrouter/free", model)
        self.assertEqual(200, attempts[0]["http_status"])
        self.assertEqual(503, attempts[0]["error_code"])
        self.assertEqual("provider_overloaded", attempts[0]["error_type"])
        self.assertEqual("example/free-model:free", attempts[1]["actual_model"])
        self.assertEqual([3], [call.args[0] for call in sleeps])

    def test_secrets_and_prompt_are_not_persisted(self):
        payload = {"error": {"code": 404, "message": "private excerpt " + self.keys["GEMINI_API_KEY"] + self.keys["OR_API_KEY"],
                             "metadata": {"error_type": self.keys["OR_API_KEY"]}}}
        _, _, audit, _, _, diagnostics = self.select([self.error(404, payload=payload), self.response("openrouter")])
        persisted = json.dumps(audit)
        self.assertNotIn("private excerpt", persisted)
        for key in self.keys.values():
            self.assertNotIn(key, persisted)
            self.assertNotIn(key, diagnostics)

    def test_missing_keys_and_paid_models_are_skipped(self):
        for keys, policy in (({}, self.policy), ({"OR_API_KEY": self.keys["OR_API_KEY"]},
                {**self.policy, "models": {"gemini": "gemini-3.8-flash", "openrouter": ["paid/model"]}})):
            _, model, audit, calls, sleeps, _ = self.select([], keys, policy)
            self.assertEqual("failed", model)
            self.assertEqual([], calls)
            self.assertTrue(all(record["outcome"] == "skipped" for record in audit))

    def test_valid_empty_selection_does_not_trigger_fallback(self):
        empty = {"europe_now": [], "top_stories": []}
        selected, model, audit, calls, sleeps, _ = self.select([self.response(selection=empty)])
        self.assertEqual(empty, selected)
        self.assertTrue(model.startswith("gemini:"))
        self.assertEqual(1, len(calls))

    def test_malformed_response_falls_back_without_retry(self):
        _, model, audit, calls, sleeps, _ = self.select([JsonResponse(b'[]'), self.response("openrouter")])
        self.assertEqual("openrouter:openrouter/free", model)
        self.assertEqual(2, len(calls))
        self.assertEqual([], sleeps)

    def test_collector_persists_attempts_and_keeps_previous_selection(self):
        now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
        at = news_main.iso(now)
        article = news_main.Article("article-1", "source-1", "Source", "Germany election", "Private excerpt",
                                   "https://example.com/germany", at, at, [])
        previous = {"europe_now": [], "top_stories": [{"title": "Germany election", "summary": "A. B.",
            "sources": [{"publisher": "Source", "url": article.url}], "source_count": 1}]}
        policy = {**self.policy, "seen_window_hours": 48, "radar_window_hours": 24,
                  "max_radar_items": 180, "max_llm_items": 45}
        state = {"seen": {}, "radar": [], "recent_topics": [], "last_selection": previous}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(news_main, ROOT=root, STATE_PATH=root / "state.json",
                                PUBLIC_PATH=root / "public.json", LOG_DIR=root / "logs"):
                with patch.object(news_main, "read_yaml", side_effect=[policy, {"sources": [{}]}]), \
                     patch.object(news_main, "utcnow", return_value=now), \
                     patch.object(news_main, "load_state", return_value=state), \
                     patch.object(news_main, "due_to_collect", return_value=True), \
                     patch.object(news_main, "collect_source", return_value=([article], {})), \
                     patch.object(news_main, "developing_panel", return_value=[]), \
                     patch.object(news_main.archive, "record"), \
                     patch.dict(os.environ, self.keys, clear=True), \
                     patch.object(news_main, "urlopen", side_effect=[self.error(404), self.error(404)]), \
                     redirect_stderr(io.StringIO()):
                    news_main.main()
            audit = json.loads((root / "logs/2026-10.jsonl").read_text(encoding="utf-8"))
            self.assertEqual("failed-kept-previous", audit["model"])
            self.assertEqual([404, 404], [record["error_code"] for record in audit["llm_attempts"]])
            public = json.loads((root / "public.json").read_text(encoding="utf-8"))
            self.assertEqual(previous["top_stories"], public["top_stories"])
            self.assertNotIn("llm_attempts", public)
            self.assertNotIn("Private excerpt", (root / "state.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
