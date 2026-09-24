"""Pipeline: source → LLM → validate → feedback retry → escalate → audit.

The pipeline owns the retry loop explicitly: after every failed attempt the
exact pydantic validation errors (including user cross-field validator
messages) and the model's previous output are appended to the prompt context.
When validation keeps failing, source context is re-injected (DOM regions
matching the schema's css hints, or matching PDF pages) before giving up with
a fully audited ``ExtractionFailed``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from extractor.audit import AttemptRecord, RunTrace, new_run_id
from extractor.cache import CacheKey, ResultCache, canonical_fingerprint
from extractor.config import Settings
from extractor.errors import ExtractionFailed, SourceError
from extractor.llm.base import BackendCall
from extractor.llm.instructor_backend import normalize_model_string
from extractor.llm.prompts import build_feedback_message, build_messages, excerpt
from extractor.llm.registry import make_backend
from extractor.schemas.base import BaseExtraction, Provenance
from extractor.sources.base import PageContent, SourceDocument
from extractor.version import PROMPT_VERSION

BASE = "base"
ESC_DOM = "escalation:dom"
ESC_IMAGE = "escalation:image"


@dataclass
class ExtractionOptions:
    """Per-request overrides (all default from Settings)."""

    model: str = ""
    temperature: float | None = None
    seed: int | None = None  # sentinel via use_default_seed
    max_attempts: int = 0
    max_escalations: int = 0
    backend: str = ""
    use_cache: bool = True
    audit: bool = True
    use_default_seed: bool = True


@dataclass
class ExtractionResult:
    """Validated entity + provenance + audit envelope."""

    data: BaseExtraction
    provenance: Provenance
    run_trace: RunTrace


@dataclass
class _Outcome:
    best: BackendCall | None = None
    best_attempt: AttemptRecord | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)


class ExtractionAgent:
    """Public entry point: extract validated entities from URLs / PDFs / text."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        backend: Any = None,
        registry: Any = None,
    ) -> None:
        self.settings = settings or Settings()
        self.backend = backend or make_backend(self.settings)
        if registry is None:
            from extractor.schemas.registry import SchemaRegistry

            registry = SchemaRegistry()
        self.registry = registry
        self.cache = ResultCache(self.settings.cache_dir, self.settings.cache_ttl_hours)

    # ── public API ──────────────────────────────────────────────────────────
    async def extract_url(
        self,
        url: str,
        *,
        schema_name: str = "",
        schema: type[BaseExtraction] | None = None,
        options: ExtractionOptions | None = None,
    ) -> ExtractionResult:
        from extractor.sources.html import HtmlSource

        opts = options or ExtractionOptions()
        doc = await HtmlSource(self.settings, url).fetch()
        return await self.extract_document(doc, schema_name=schema_name, schema=schema, options=opts)

    async def extract_html_text(
        self,
        text: str,
        *,
        origin: str = "inline://html",
        schema_name: str = "",
        schema: type[BaseExtraction] | None = None,
        options: ExtractionOptions | None = None,
    ) -> ExtractionResult:
        """Extract from raw HTML text (no browser) — used by tests/harness."""
        from extractor.sources.prune import prune_dom

        opts = options or ExtractionOptions()
        doc = SourceDocument(
            kind="html",
            origin=origin,
            raw_sha256=canonical_fingerprint(text),
            pages=[PageContent(text=prune_dom(text), page_number=1)],
        )
        return await self.extract_document(doc, schema_name=schema_name, schema=schema, options=opts)

    async def extract_pdf_file(
        self,
        path: Any,
        *,
        schema_name: str = "",
        schema: type[BaseExtraction] | None = None,
        options: ExtractionOptions | None = None,
    ) -> ExtractionResult:
        from extractor.sources.pdf import pdf_from_file

        opts = options or ExtractionOptions()
        data, origin = pdf_from_file(self.settings, path)
        return await self.extract_pdf_bytes(data, origin=origin, schema_name=schema_name, schema=schema, options=opts)

    async def extract_pdf_bytes(
        self,
        data: bytes,
        *,
        origin: str = "upload.pdf",
        schema_name: str = "",
        schema: type[BaseExtraction] | None = None,
        options: ExtractionOptions | None = None,
    ) -> ExtractionResult:
        from extractor.sources.pdf import PdfSource

        opts = options or ExtractionOptions()
        doc = await PdfSource(self.settings, data, origin).fetch()
        return await self.extract_document(doc, schema_name=schema_name, schema=schema, options=opts)

    async def extract_document(
        self,
        doc: SourceDocument,
        *,
        schema_name: str = "",
        schema: type[BaseExtraction] | None = None,
        options: ExtractionOptions | None = None,
    ) -> ExtractionResult:
        opts = options or ExtractionOptions()
        resolved = self._resolve_schema(schema, schema_name)

        trace = self._new_trace(doc, resolved, opts)
        model = opts.model or self.settings.model
        temperature = self.settings.temperature if opts.temperature is None else opts.temperature
        seed = self.settings.seed if opts.use_default_seed else opts.seed
        max_attempts = opts.max_attempts or self.settings.max_attempts
        max_escalations = opts.max_escalations or self.settings.max_escalations

        cache_key = self._cache_key(doc, resolved, model, opts, temperature, seed)
        trace.cache_key = cache_key.as_string()

        if opts.use_cache:
            cached = self.cache.get(trace.cache_key)
            if cached is not None:
                trace.cache_hit = True
                trace.outcome = "cache_hit"
                trace.completed_at = _now()
                return self._cached_result(cached, trace, resolved)

        pages = self._prepare_pages(doc)
        outcome = await self._run_loop(
            trace, doc, resolved, pages, model, temperature, seed, max_attempts, max_escalations
        )  # noqa: E501

        if outcome.best is not None and outcome.best.parsed is not None:
            parsed = outcome.best.parsed
            assert isinstance(parsed, BaseExtraction), "backend returned wrong model"
            merged = self.merge_instances([parsed], schema=resolved)
            result = self._finalize(merged, trace, doc, resolved, model, outcome, audit_enabled=opts.audit)
            if opts.use_cache:
                self.cache.put(trace.cache_key, self._cache_payload(result))
            return result

        if opts.audit:
            self._write_audit(trace, enabled=True)
        trace.outcome = "failed"
        trace.completed_at = _now()
        trace.error = f"all {len(outcome.attempts)} attempt(s) failed validation"
        raise ExtractionFailed(
            f"extraction failed after {len(outcome.attempts)} attempt(s) "
            f"(schema={resolved.schema_name}, source={doc.origin}); see run trace",
            run_trace=trace,
        )

    # ── retry loop with validation feedback ─────────────────────────────────
    async def _run_loop(
        self,
        trace: RunTrace,
        doc: SourceDocument,
        schema: type[BaseExtraction],
        pages: list[PageContent],
        model: str,
        temperature: float,
        seed: int | None,
        max_attempts: int,
        max_escalations: int,
    ) -> _Outcome:
        """Run attempts; append validation errors + previous output to context."""
        outcome = _Outcome()
        # scanned pages (no text layer) are attached as images for vision models
        images = [
            {"mime": p.image_mime, "data_b64": p.image_base64}
            for p in pages
            if p.image_base64
        ]
        base_messages = build_messages(
            schema=schema,
            source_text="\n\n".join(p.text for p in pages) or "(text-free document; see attached page images)",
            source_kind=doc.kind,
            source_origin=doc.origin,
            images=images or None,
        )
        messages = [dict(m) for m in base_messages]

        current_escalation = ""  # re-injected context the NEXT attempt will see
        for index in range(1, max_attempts + 1):
            kind = BASE
            if index > 1 and current_escalation:
                kind = ESC_IMAGE if doc.kind == "pdf" else ESC_DOM
            call, record = await self._run_attempt(
                trace, messages, schema, model, temperature, seed, index, kind
            )
            outcome.attempts.append(record)

            if call.parsed is not None:
                record.ok = True
                outcome.best = call
                outcome.best_attempt = record
                break

            # ── the feedback step: errors + previous output -> prompt context ──
            errors = call.validation_errors or [call.error or "unknown error"]
            record.validation_errors = errors
            if outcome.best is None or len(errors) < len(
                outcome.best_attempt.validation_errors if outcome.best_attempt else []
            ):
                outcome.best = call
                outcome.best_attempt = record

            # From attempt 2 on, re-inject source context (DOM region / page
            # marker) alongside the validation feedback, until escalations run
            # out; later retries keep plain error feedback.
            escalation = ""
            if index < max_attempts and (index - 1) < max_escalations:
                escalation = self._escalation_prefix(doc, pages, schema)
            current_escalation = escalation
            messages = [dict(m) for m in base_messages]
            messages.append(
                build_feedback_message(
                    errors=errors,
                    previous_output=call.raw_text,
                    escalation_context=escalation or None,
                )
            )

        trace.attempts = outcome.attempts
        return outcome

    async def _run_attempt(
        self,
        trace: RunTrace,
        messages: list[dict[str, Any]],
        schema: type[BaseExtraction],
        model: str,
        temperature: float,
        seed: int | None,
        index: int,
        kind: str,
    ) -> tuple[BackendCall, AttemptRecord]:
        last_user_text = _last_user_text(messages)
        record = AttemptRecord(
            index=index,
            kind=kind,
            model=model,
            backend=getattr(self.backend, "name", str(type(self.backend).__name__)),
            prompt_chars=sum(len(str(m.get("content", ""))) for m in messages),
            prompt_excerpt=excerpt(last_user_text),
        )
        try:
            call = await self.backend.call(
                messages=messages,
                schema=schema,
                model=model,
                temperature=temperature,
                seed=seed,
            )
        except Exception as exc:
            call = BackendCall(
                error=f"{type(exc).__name__}: {exc}",
                model_used=model,
                backend_name=getattr(self.backend, "name", "?"),
            )
        record.completed_at = _now()
        record.raw_output = call.raw_text
        record.token_usage = call.token_usage
        record.cost_usd = call.cost_usd
        record.error = call.error
        trace.total_cost_usd += call.cost_usd or 0.0
        trace.model = normalize_model_string(model)
        trace.backend = record.backend
        return call, record

    # ── escalation: re-inject source context on retries ──────────────
    def _escalation_prefix(
        self, doc: SourceDocument, pages: list[PageContent], schema: type[BaseExtraction]
    ) -> str:
        """Source context re-injected with validation feedback on retries.

        HTML: the pruned-DOM region matching the schema's css hints.
        PDF: a marker noting weak-text pages whose images are attached.
        """
        if doc.kind == "pdf":
            poor = [p for p in pages if p.needs_vision or len(p.text) < 200]
            if poor:
                return "(pdf pages with weak text layers; page images were attached)"
            return ""
        return _locate_region(pages[0].text, schema.css_hints)

    # ── merging ──────────────────────────────────────────────────────────────
    def merge_instances(
        self, instances: list[BaseExtraction], *, schema: type[BaseExtraction]
    ) -> BaseExtraction:
        """Merge per-page/per-section instances; revalidate the merged result."""
        if len(instances) == 1:
            return instances[0]
        if hasattr(schema, "merge_strategy") and schema.merge_strategy == "concatenate_items":
            merged_data: dict[str, Any] = {}
            for inst in instances:
                data = inst.model_dump()
                for key, value in data.items():
                    if isinstance(value, list):
                        merged_data.setdefault(key, []).extend(value)
                    elif value is not None:
                        merged_data.setdefault(key, value)
            try:
                return schema.model_validate(merged_data)
            except Exception:
                return instances[0]
        # default: first non-empty instance wins (fields set, not None/empty)
        best = instances[0]
        best_score = _filled_score(best)
        for inst in instances[1:]:
            score = _filled_score(inst)
            if score > best_score:
                best, best_score = inst, score
        try:
            return schema.model_validate(best.model_dump())
        except Exception:
            return best

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _resolve_schema(
        self, schema: type[BaseExtraction] | None, schema_name: str
    ) -> type[BaseExtraction]:
        if schema is not None:
            self.registry.register(schema)
            return schema
        if not schema_name:
            # No schema requested: fall back to the schema-agnostic "general"
            # auto-analyze schema so any document can be analyzed as-is.
            return self.registry.get("general")
        return self.registry.get(schema_name)

    def _new_trace(
        self, doc: SourceDocument, schema: type[BaseExtraction], opts: ExtractionOptions
    ) -> RunTrace:
        return RunTrace(
            run_id=new_run_id("extract"),
            source_kind=doc.kind,
            source_origin=doc.origin,
            source_sha256=doc.raw_sha256,
            schema_name=schema.schema_name,
            schema_sha256=schema.schema_sha256(),
            model=opts.model or self.settings.model,
            backend=opts.backend or self.settings.backend,
        )

    def _prepare_pages(self, doc: SourceDocument) -> list[PageContent]:
        pages = [p for p in doc.pages if p.text.strip()]
        if not pages:
            pages = [p for p in doc.pages if p.image_base64]
        if not pages:
            raise SourceError(f"source document has no extractable content: {doc.origin}")
        return pages

    def _cache_key(
        self,
        doc: SourceDocument,
        schema: type[BaseExtraction],
        model: str,
        opts: ExtractionOptions,
        temperature: float,
        seed: int | None,
    ) -> CacheKey:
        return CacheKey(
            source_sha256=doc.raw_sha256,
            schema_sha256=schema.schema_sha256(),
            model=normalize_model_string(model),
            backend=opts.backend or self.settings.backend,
            prompt_version=PROMPT_VERSION,
            options_fingerprint=canonical_fingerprint(
                {"temperature": temperature, "seed": seed}
            ),
        )

    def _finalize(
        self,
        data: BaseExtraction,
        trace: RunTrace,
        doc: SourceDocument,
        schema: type[BaseExtraction],
        model: str,
        outcome: _Outcome,
        audit_enabled: bool = True,
    ) -> ExtractionResult:
        trace.outcome = "success"
        trace.completed_at = _now()
        trace.result_json = data.model_dump(mode="json")
        attempt_count = len(outcome.attempts)
        prov = Provenance(
            source_kind=doc.kind,
            source_origin=doc.origin,
            source_sha256=doc.raw_sha256,
            schema_name=schema.schema_name,
            schema_sha256=schema.schema_sha256(),
            model=normalize_model_string(model),
            backend=trace.backend,
            prompt_version=PROMPT_VERSION,
            extracted_at=_now().isoformat(),
            attempt_count=attempt_count,
            cache_hit=False,
            run_id=trace.run_id,
            pages=[p.page_number for p in doc.pages],
        )
        self._write_audit(trace, enabled=audit_enabled)
        return ExtractionResult(data=data, provenance=prov, run_trace=trace)

    def _cached_result(
        self,
        payload: dict[str, Any],
        trace: RunTrace,
        schema: type[BaseExtraction],
    ) -> ExtractionResult:
        data = schema.model_validate(payload["data"])
        prov = Provenance(**payload["provenance"])
        prov.cache_hit = True
        trace.outcome = "cache_hit"
        trace.completed_at = _now()
        return ExtractionResult(data=data, provenance=prov, run_trace=trace)

    def _cache_payload(self, result: ExtractionResult) -> dict[str, Any]:
        return {
            "data": result.data.model_dump(mode="json"),
            "provenance": result.provenance.model_dump(mode="json"),
            "run_id": result.run_trace.run_id,
        }

    def _write_audit(self, trace: RunTrace, enabled: bool = True) -> None:
        if enabled and self.settings.audit_dir:
            trace.to_jsonl(self.settings.audit_dir)


def _filled_score(inst: BaseExtraction) -> int:
    score = 0
    for value in inst.model_dump().values():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        score += 1
    return score


def _locate_region(pruned_dom: str, hints: list[str], context: int = 2400) -> str:
    """Find a region of the pruned DOM matching schema css hints.

    Matches simple id/class substrings from the hints against tag attributes.
    """
    if not hints:
        return ""
    for hint in hints:
        token = hint.rstrip("]").strip(".#") or hint
        for line in pruned_dom.splitlines():
            if token and token.lower() in line.lower() and line.lstrip().startswith("<"):
                start = max(0, line_idx(pruned_dom, line) - context // 2)
                end = min(len(pruned_dom), start + context * 2)
                return pruned_dom[start:end]
    return ""


def line_idx(text: str, line: str) -> int:
    idx = text.find(line)
    return idx if idx >= 0 else 0


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    str(p.get("text", "")) for p in content if isinstance(p, dict)
                )
    return ""


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)
