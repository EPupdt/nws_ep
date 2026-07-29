# Europe Pulse News Agent

Automated, English-language Europe news monitoring for Europe Pulse.

The agent collects public RSS feeds, normalises and de-duplicates items, publishes a transparent News Radar, and uses Gemini with an OpenRouter fallback for an editorially constrained selection of **Europe Now** and **Top stories**.

## Safety and editorial boundaries

- The agent links to original publishers; it does not republish full articles.
- `Europe Now` requires European relevance plus urgency, public impact, or systemic significance.
- The LLM may only use supplied titles, excerpts, links, timestamps and recent-topic context.
- Invalid model output never stops collection or publishing.
- API keys belong only in GitHub Actions secrets: `GEMINI_API_KEY` and `OR_API_KEY`.

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

The workflow in `.github/workflows/news-hub.yml` runs every 15 minutes, prevents overlapping runs, and commits only operational state, audit logs and generated public output. The first run can be started from **Actions → Europe Pulse News Hub → Run workflow**.

Before using GitHub Pages, configure the repository's Pages source as **Deploy from a branch → main → /docs**. This makes the preview available at `https://epupdt.github.io/nws_ep/`; it is not yet the EuropePulse.eu integration.
