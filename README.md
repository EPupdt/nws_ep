# Europe Pulse News Agent

Automated, English-language Europe news monitoring for Europe Pulse.

The agent collects public RSS feeds, normalises and de-duplicates items, publishes a transparent News Radar, and uses Gemini with an OpenRouter fallback for an editorially constrained selection of **Europe Now** and **Top stories**.

## Safety and editorial boundaries

- The agent links to original publishers; it does not republish full articles.
- `Europe Now` requires European relevance plus urgency, public impact, or systemic significance.
- The LLM may only use supplied titles, excerpts, links, timestamps and recent-topic context.
- Invalid model output never stops collection or publishing.
- API keys belong only in GitHub Actions secrets: `GEMINI_API_KEY` and `OR_API_KEY`.
- Editorial publishers are `title_link_only` by default; see [SOURCES.md](SOURCES.md).

## Run locally

```powershell
py -m pip install -r requirements.txt
$env:GEMINI_API_KEY = "..."
$env:OR_API_KEY = "..."
$env:PYTHONPATH = "src"
py -m news_hub.main
```

The runnable result is written to `docs/data/news-hub.json`; the static dashboard is in `docs/index.html`.

## GitHub Actions

The workflow is triggered every 15 minutes by the external cron-job.org job, evaluates editorial hours in `Europe/Bratislava`, prevents overlapping runs, and commits only operational state, audit logs and generated public output. GitHub Actions has no internal schedule trigger. A manual **Actions → Europe Pulse News Hub → Run workflow** only bypasses the editorial schedule when its `force` input is selected.

For migration, architecture, active diagnostics and the WordPress roadmap, see [HANDOFF_NEW_PC.md](HANDOFF_NEW_PC.md).

## LLM retries and audit

The primary model is the stable `gemini-3.8-flash`, which supports JSON output and has a Gemini API Free Tier. Keep the Google project on Free Tier to preserve zero-cost operation; API keys inherit their project billing tier. OpenRouter remains the fallback and accepts only `openrouter/free` or explicit `:free` IDs. Each model retries transient 429/503 errors at most twice, using exponential backoff with jitter. `Retry-After` supports seconds and HTTP dates. The cumulative retry wait is limited to 60 seconds per model; when the provider asks for a longer wait, collection proceeds to fallback instead of retrying early. Recognised daily quota exhaustion and 404 errors go directly to fallback. Settings are in `config/policy.yml` under `llm_retry`.

OpenRouter error objects are handled even with HTTP 200. Each monthly selection-log entry includes `llm_attempts`: requested and actual model (when returned), attempt number, outcome, HTTP status, provider error code/type and retry delay. Missing keys and excluded non-free models are recorded as skipped. Raw response/error bodies, prompts and credentials are not stored in this audit. The existing final `model` field and keep-previous-selection behavior remain compatible.

Before using GitHub Pages, configure the repository's Pages source as **Deploy from a branch → main → /docs**. This makes the preview available at `https://epupdt.github.io/nws_ep/`; it is not yet the EuropePulse.eu integration.
