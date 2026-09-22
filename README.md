# extractor — deterministic structured-data extraction agent

Crawl **changing DOM structures** (Playwright) and **unstructured PDFs**
(PyMuPDF + vision fallback) and reliably extract entities into **strict
Pydantic schemas** — with schema-validation failures fed back into the prompt
context until the output conforms, or a fully audited failure is raised.

```
Playwright / PyMuPDF ──▶ pruned DOM / page text (+images) ──▶ LLM (LiteLLM)
                                   ▲                              │
                                   │        schema errors +       ▼
                                   └──── previous output ◀── Pydantic validation
```

## Windows app — download and install directly

No Python required: grab the prebuilt Windows app from the
[**Releases**](https://github.com/Vikrant-Mohan/data-restructuring-project/releases/latest)
page. Two options:

- **Installer (recommended)** — download `ExtractorConsole-Setup-*.exe` and run
  it. Installs to Program Files with a Start Menu shortcut and a proper
  uninstaller (Apps & features → "Extractor Console").
- **Portable zip** — download `ExtractorConsole-v*-win64.zip`, unzip it
  anywhere, and run `ExtractorConsole.exe`. Keep the exe and the `_internal`
  folder together.

- Set `EXTRACT_MODEL` and your provider API key via environment variables, or a
  `.env` file placed next to the exe (see `.env.example`).
- Closing the console window shuts the app down.

## Install

```bash
pip install -e ".[dev]"
playwright install chromium          # browser binaries for the HTML source
cp .env.example .env                 # then set your provider key(s)
```

## Quickstart

```bash
# CLI
extractor run https://example.com/product --schema product
extractor run invoice.pdf --schema-file my_schemas/invoice.py
extractor schemas                     # list registered schemas
extractor schema-new job_posting      # scaffold a new schema
extractor serve --port 8000           # HTTP API
```

```python
import asyncio
from extractor import ExtractionAgent, Settings
from extractor.schemas.examples import Product

agent = ExtractionAgent(Settings())
result = asyncio.run(agent.extract_url("https://example.com/p", schema_name="product"))
print(result.data.name, result.data.price)
print(result.provenance.model_dump_json(indent=2))
```

## HTTP API

```bash
curl -s localhost:8000/extract -H 'content-type: application/json' -d '{
  "url": "https://example.com/product",
  "schema_name": "product",
  "include_audit": true
}'
```

- `POST /extract` — `{url | html | pdf_base64}`, `schema_name`, optional
  `model` / `backend` / `temperature` / `seed` / `max_attempts`, `use_cache`,
  `include_audit`
- `POST /extract/upload` — multipart PDF/HTML upload
- `GET /schemas`, `GET /health`

Validation failures after all retries map to **HTTP 422** with the full
attempt list; provider problems map to **502**.

## How it stays deterministic

| Mechanism | Where |
|---|---|
| SHA-256 of raw source + schema in every result | `Provenance` |
| Cache key = source ⊕ schema ⊕ model ⊕ backend ⊕ prompt version ⊕ options | `cache.py` |
| `temperature=0`, fixed `seed`, versioned prompts | `Settings`, `version.py` |
| Pruned-DOM digest is pure-Python and stable | `sources/prune.py` |
| JSONL audit of every attempt (prompt excerpt, errors, tokens, cost) | `audit.py` |

## The validation-feedback loop

1. Attempt 1 sends the pruned DOM / PDF text + JSON Schema + validator
   contract.
2. Pydantic (including your `@model_validator` cross-field rules) validates
   the output.
3. On failure the pipeline re-issues the prompt with a feedback message:
   the **exact validation errors** and the **previous raw output**.
4. From attempt 2 on, it also re-injects source context: the pruned-DOM
   region matching the schema's `css_hints`, or (PDFs) a marker for
   weak-text pages whose images are attached.
5. Exhausted retries raise `ExtractionFailed` carrying the full `RunTrace`
   (also written to `audit/<run-id>.jsonl` for replay).

## DOM-drift resilience

No fixed selectors: the model reads a pruned, stable digest of the DOM, so
renamed classes and moved nodes don't break extraction. The golden harness
proves it:

```
golden/
  product_page.html          # recorded page
  product_page.golden.json   # expected output
  product_page.mutants.json  # [{"regex": ..., "replace": ...}] simulating drift
```

```bash
extractor golden golden/ --schema product          # replay + mutants must match
extractor golden golden/ --schema product --update # re-record
```

## Backends

- **instructor** (default) — any LiteLLM provider string:
  `openai/gpt-4o-mini`, `anthropic/claude-3-5-sonnet`, `ollama/llama3.1`, …
- **outlines** (opt-in, `pip install -e ".[outlines]"`) — grammar-constrained
  decoding for local models: `transformers/<hf-id>`, `ollama/<model>`,
  `vllm/<model>`. Invalid JSON becomes nearly impossible; semantic
  cross-field validators still feed back through the same loop.

## Scanned PDFs

Pages below `EXTRACT_PDF_TEXT_DENSITY` chars/in² are rendered to PNG and sent
as images to `EXTRACT_VISION_MODEL` (multimodal messages). Text-layer pages
stay cheap and fully deterministic.

## Deploying to Vercel

The FastAPI service deploys as a single Vercel Function (Python runtime).
Configuration already committed:

- `main.py` — root entrypoint shim; exports `app` at a recognized location and
  makes the src-layout package importable.
- `pyproject.toml` — `[tool.vercel] entrypoint = "main:app"` (disambiguates the
  other `app` variables in this repo: CLI, tests) + runtime env defaults.
- `.python-version` — pins the Vercel runtime to Python 3.12.
- `vercel.json` — keeps tests/caches out of the function bundle.

Two serverless constraints are handled for you:

1. **Read-only filesystem** — cache/audit/user-schema dirs automatically fall
   back to `/tmp` when the working directory is not writable.
2. **No Chromium** — serverless functions cannot run Playwright's browser, so
   URL extraction transparently falls back to a static HTTP fetch (same
   pruning + extraction pipeline, no JS rendering). Force it everywhere with
   `EXTRACT_DISABLE_BROWSER=1`.

Deploy:

```bash
npm i -g vercel
vercel link
vercel env add EXTRACT_MODEL          # e.g. litellm/gpt-4o-mini
vercel env add OPENAI_API_KEY         # provider key(s) your model string needs
vercel --prod
```

Or push to GitHub and import the repo at vercel.com/new — detection is zero-config.

Smoke test after deploying:

```bash
curl -s https://<your-app>.vercel.app/health
curl -s https://<your-app>.vercel.app/extract -H 'content-type: application/json' \
  -d '{"url": "https://example.com", "schema_name": "product"}'
```

## Testing

```bash
pytest            # no network: scripted fake LLMs + real fixtures (PyMuPDF-built PDFs)
mypy              # strict-ish typing
ruff check .
```

The core acceptance test (`tests/test_feedback_loop.py`) proves a scripted
LLM that returns schema-invalid output first recovers on attempt 2, with the
validation error text verifiably present in attempt 2's prompt.
