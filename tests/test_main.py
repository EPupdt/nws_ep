import io
import json
import os
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch
from urllib.error import HTTPError

from news_hub import main as news_main


class JsonResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class LlmSelectionTests(unittest.TestCase):
    policy = {
        "max_europe_now": 2,
        "top_story_count": 5,
        "models": {"gemini": "gemini-2.5-flash-lite", "openrouter": ["openrouter/free"]},
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


if __name__ == "__main__":
    unittest.main()
