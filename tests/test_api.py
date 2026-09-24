"""API tests via httpx ASGI transport (no network, no LLM)."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from extractor.api.main import app, pool
from tests.fixtures.pages import PRODUCT_HTML, text_pdf_bytes
from tests.fixtures.responses import valid_product


class _FakeAgent:
    def __init__(self) -> None:
        from extractor.schemas.registry import SchemaRegistry

        self.registry = SchemaRegistry()
        self.calls: list[dict[str, Any]] = []

    async def extract_url(self, url: str, **kw: Any) -> Any:
        self.calls.append({"kind": "url", **kw})
        return await self._result()

    async def extract_html_text(self, html: str, **kw: Any) -> Any:
        self.calls.append({"kind": "html", **kw})
        return await self._result()

    async def extract_pdf_bytes(self, data: bytes, **kw: Any) -> Any:
        result = await self._result()
        result.provenance.source_kind = "pdf"
        result.provenance.source_origin = "request:upload.pdf"
        result.provenance.pages = [1]
        return result

    async def _result(self) -> Any:
        from tests.helpers import fake_result

        return fake_result(valid_product())


class _FailingAgent(_FakeAgent):
    async def extract_html_text(self, html: str, **kw: Any) -> Any:
        from extractor.audit import AttemptRecord, RunTrace, new_run_id
        from extractor.errors import ExtractionFailed

        run_id = new_run_id("extract")
        trace = RunTrace(
            run_id=run_id,
            source_kind="html",
            source_origin="request:html",
            source_sha256="0" * 64,
            schema_name="product",
            schema_sha256="1" * 64,
            model="test-model",
            backend="instructor",
            outcome="failed",
            attempts=[
                AttemptRecord(
                    index=1,
                    model="test-model",
                    backend="instructor",
                    ok=False,
                    validation_errors=["output was not valid JSON"],
                )
            ],
        )
        raise ExtractionFailed("all attempts failed", run_trace=trace)


@pytest.fixture
def fake_pool() -> None:
    pool._agent = _FakeAgent()  # type: ignore[assignment]
    yield
    pool._agent = None  # type: ignore[assignment]


@pytest.fixture
def failing_pool() -> None:
    pool._agent = _FailingAgent()  # type: ignore[assignment]
    yield
    pool._agent = None  # type: ignore[assignment]


@pytest.fixture
def client() -> Any:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health_lists_schemas(client: Any, fake_pool: None) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert "product" in resp.json()["schemas"]
    assert "general" in resp.json()["schemas"]  # built-in auto-analyze schema


async def test_empty_schema_name_passes_through_to_agent(client: Any, fake_pool: None) -> None:
    """No schema requested: the API forwards '' and the pipeline picks `general`."""
    resp = await client.post("/extract", json={"html": "<p>x</p>"})
    assert resp.status_code == 200
    assert pool._agent.calls[0]["schema_name"] == ""  # type: ignore[union-attr]


async def test_extract_from_html(client: Any, fake_pool: None) -> None:
    resp = await client.post("/extract", json={"html": PRODUCT_HTML, "schema_name": "product"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["name"] == "Blue Widget"
    assert body["provenance"]["schema_name"] == "product"
    assert body["audit"] is None


async def test_extract_includes_audit_when_requested(client: Any, fake_pool: None) -> None:
    resp = await client.post(
        "/extract", json={"html": "<p>x</p>", "schema_name": "product", "include_audit": True}
    )
    assert resp.status_code == 200
    assert resp.json()["audit"]["outcome"] == "success"


async def test_extract_pdf_base64(client: Any, fake_pool: None) -> None:
    b64 = base64.b64encode(text_pdf_bytes()).decode()
    resp = await client.post(
        "/extract", json={"pdf_base64": b64, "schema_name": "invoice"}
    )
    assert resp.status_code == 200
    assert resp.json()["provenance"]["source_kind"] == "pdf"


async def test_extract_requires_input(client: Any, fake_pool: None) -> None:
    resp = await client.post("/extract", json={"schema_name": "product"})
    assert resp.status_code == 400


async def test_extraction_failure_maps_to_422(client: Any, failing_pool: None) -> None:
    resp = await client.post("/extract", json={"html": "<p>x</p>", "schema_name": "product"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "failed" in detail["error"]
    assert detail["attempts"][0]["validation_errors"] == ["output was not valid JSON"]


async def test_upload_endpoint_accepts_pdf(client: Any, fake_pool: None) -> None:
    resp = await client.post(
        "/extract/upload",
        files={"file": ("invoice.pdf", text_pdf_bytes(), "application/pdf")},
        data={"schema_name": "invoice"},
    )
    assert resp.status_code == 200
    assert resp.json()["provenance"]["source_kind"] == "pdf"
