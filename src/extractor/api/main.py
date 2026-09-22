"""FastAPI service: POST /extract from URL, HTML, or base64 PDF."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from extractor.config import Settings
from extractor.errors import ExtractionFailed, ExtractorError, SchemaNotFound
from extractor.pipeline import ExtractionAgent, ExtractionOptions

app = FastAPI(
    title="Deterministic Extraction Agent",
    version="0.1.0",
    description="Crawl changing DOM structures and unstructured PDFs into strict Pydantic schemas.",
)


class ExtractRequest(BaseModel):
    url: str | None = None
    html: str | None = None
    pdf_base64: str | None = None
    schema_name: str = ""
    model: str | None = None
    backend: str | None = None
    temperature: float | None = None
    seed: int | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    max_escalations: int | None = Field(default=None, ge=0, le=5)
    use_cache: bool = True
    include_audit: bool = False


class ProvenanceOut(BaseModel):
    source_kind: str
    source_origin: str
    source_sha256: str
    schema_name: str
    schema_sha256: str
    model: str
    backend: str
    prompt_version: str
    extracted_at: str
    attempt_count: int
    cache_hit: bool
    run_id: str
    pages: list[int]


class ExtractResponse(BaseModel):
    data: dict[str, Any]
    provenance: ProvenanceOut
    audit: dict[str, Any] | None = None


class AgentPool:
    """Lazily-built agent per settings (schemas registered once)."""

    def __init__(self) -> None:
        self._agent: ExtractionAgent | None = None
        self._settings: Settings | None = None

    def get(self) -> tuple[ExtractionAgent, Settings]:
        if self._agent is None:
            settings = Settings()
            from extractor.schemas.registry import SchemaRegistry

            registry = SchemaRegistry()
            registry.register_user_dir(settings.user_schema_dir)
            self._agent = ExtractionAgent(settings, registry=registry)
            self._settings = settings
        return self._agent, self._settings or Settings()


pool = AgentPool()


@app.get("/health")
async def health() -> dict[str, str]:
    agent, _ = pool.get()
    return {"status": "ok", "schemas": ",".join(agent.registry.names())}


@app.get("/schemas")
async def list_schemas() -> dict[str, list[str]]:
    agent, _ = pool.get()
    return {"schemas": agent.registry.names()}


@app.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest) -> ExtractResponse:
    """Extract structured data from a URL, raw HTML, or base64-encoded PDF."""
    agent, _ = pool.get()
    opts = ExtractionOptions(
        model=req.model or "",
        backend=req.backend or "",
        temperature=req.temperature,
        seed=req.seed,
        use_default_seed=req.seed is None,
        max_attempts=req.max_attempts or 0,
        max_escalations=req.max_escalations or 0,
        use_cache=req.use_cache,
    )
    schema_name = req.schema_name
    try:
        if req.url:
            result = await agent.extract_url(req.url, schema_name=schema_name, options=opts)
        elif req.html:
            result = await agent.extract_html_text(
                req.html, origin="request:html", schema_name=schema_name, options=opts
            )
        elif req.pdf_base64:
            try:
                data = base64.b64decode(req.pdf_base64, validate=True)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"invalid base64 PDF: {exc}") from exc
            result = await agent.extract_pdf_bytes(
                data, origin="request:upload.pdf", schema_name=schema_name, options=opts
            )
        else:
            raise HTTPException(status_code=400, detail="provide one of: url, html, pdf_base64")
    except ExtractionFailed as exc:
        payload: dict[str, Any] = {"error": str(exc)}
        if exc.run_trace is not None:
            payload["attempts"] = [a.model_dump(mode="json") for a in exc.run_trace.attempts]
        raise HTTPException(status_code=422, detail=payload) from exc
    except SchemaNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExtractorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ExtractResponse(
        data=result.data.model_dump(mode="json"),
        provenance=ProvenanceOut(**result.provenance.model_dump(mode="json")),
        audit=result.run_trace.model_dump(mode="json") if req.include_audit else None,
    )


@app.post("/extract/upload", response_model=ExtractResponse)
async def extract_upload(
    file: UploadFile = File(...),
    schema_name: str = "",
    include_audit: bool = False,
) -> ExtractResponse:
    """Multipart upload: PDF or HTML file."""
    agent, _ = pool.get()
    data = await file.read()
    try:
        if data[:5] == b"%PDF-":
            result = await agent.extract_pdf_bytes(
                data,
                origin=f"upload:{file.filename}",
                schema_name=schema_name,
                options=ExtractionOptions(),
            )
        else:
            result = await agent.extract_html_text(
                data.decode("utf-8", errors="replace"),
                origin=f"upload:{file.filename}",
                schema_name=schema_name,
                options=ExtractionOptions(),
            )
    except ExtractionFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExtractorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ExtractResponse(
        data=result.data.model_dump(mode="json"),
        provenance=ProvenanceOut(**result.provenance.model_dump(mode="json")),
        audit=result.run_trace.model_dump(mode="json") if include_audit else None,
    )


# Web UI + restructure routers. They import nothing from this module at import
# time (pool is imported lazily inside handlers), so there is no circularity.
# The SPA catch-all in web.py must be included LAST so API routes win.
from extractor.api.restructure import router as _restructure_router  # noqa: E402
from extractor.api.web import router as _web_router  # noqa: E402

app.include_router(_restructure_router)
app.include_router(_web_router)
