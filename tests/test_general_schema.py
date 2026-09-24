"""Tests for the schema-agnostic `general` auto-analyze schema."""

from __future__ import annotations

from typing import Any

from extractor.pipeline import ExtractionAgent, ExtractionOptions
from extractor.schemas.general import Section, StructuredDocument, SummaryRow
from tests.fixtures.pages import PRODUCT_HTML

RESUME_HTML = """\
<!DOCTYPE html>
<html>
<body>
  <main>
    <h1>Jane Doe</h1>
    <p>jane.doe@example.com | +1 555 0100 | github.com/janedoe</p>
    <h2>Summary</h2>
    <p>Backend engineer with 6 years of experience in Python and data systems.</p>
    <h2>Skills</h2>
    <ul><li>Python, FastAPI, PostgreSQL</li><li>Docker, AWS</li></ul>
    <h2>Experience</h2>
    <div>
      <h3>Senior Engineer — Acme Corp (2022–2026)</h3>
      <p>Built the event pipeline processing 2M events/day.</p>
    </div>
    <div>
      <h3>Engineer — Globex (2019–2022)</h3>
      <p>Maintained the billing service; cut p99 latency 40%.</p>
    </div>
    <h2>Projects</h2>
    <ul><li>Open-source CSV linter (1.2k stars)</li></ul>
    <h2>Education</h2>
    <p>B.Tech in Computer Science, State University, 2019</p>
  </main>
</body>
</html>
"""


def valid_general_document() -> StructuredDocument:
    """A realistic LLM analysis of the resume fixture above."""
    return StructuredDocument(
        document_type="resume",
        title="Jane Doe",
        sections=[
            Section(
                title="Summary",
                summary="Backend engineer, 6 years of experience.",
                bullets=[],
                rows=[],
            ),
            Section(
                title="Skills",
                bullets=["Python, FastAPI, PostgreSQL", "Docker, AWS"],
            ),
            Section(
                title="Experience",
                bullets=[
                    "Senior Engineer — Acme Corp (2022–2026): event pipeline, 2M events/day",
                    "Engineer — Globex (2019–2022): billing service, p99 latency -40%",
                ],
            ),
            Section(
                title="Projects",
                bullets=["Open-source CSV linter (1.2k stars)"],
            ),
            Section(
                title="Education",
                rows=[SummaryRow(label="Degree", value="B.Tech CSE")],
            ),
        ],
    )


async def test_empty_schema_name_resolves_to_general(settings: Any) -> None:
    """The no-schema request falls back to the built-in auto-analyze schema."""
    agent = ExtractionAgent(settings, backend=scripted_backend_any())
    resolved = agent._resolve_schema(None, "")
    assert resolved is StructuredDocument
    assert resolved.schema_name == "general"


def scripted_backend_any() -> Any:
    from tests.conftest import ScriptedBackend

    return ScriptedBackend([valid_general_document()])


async def test_extract_without_schema_auto_analyzes_resume(
    settings: Any, scripted_backend: Any
) -> None:
    """A resume goes in; organized sections (skills/experience/projects) come out."""
    backend = scripted_backend([valid_general_document()])
    agent = ExtractionAgent(settings, backend=backend)

    result = await agent.extract_html_text(
        RESUME_HTML, options=ExtractionOptions(use_cache=False, audit=False)
    )

    doc = result.data
    assert isinstance(doc, StructuredDocument)
    assert result.provenance.schema_name == "general"
    assert doc.document_type == "resume"
    titles = [s.title for s in doc.sections]
    assert "Skills" in titles
    assert "Experience" in titles
    assert "Projects" in titles
    experience = next(s for s in doc.sections if s.title == "Experience")
    assert any("Acme Corp" in b for b in experience.bullets)
    # the backend received the general schema and the resume text
    assert backend.calls[0]["schema"] is StructuredDocument
    assert "Jane Doe" in backend.last_user_text()
    assert "Senior Engineer" in backend.last_user_text()


async def test_general_prompt_carries_organize_instruction(
    settings: Any, scripted_backend: Any
) -> None:
    """The schema description tells the model to organize, not just dump."""
    backend = scripted_backend([valid_general_document()])
    agent = ExtractionAgent(settings, backend=backend)
    await agent.extract_html_text(
        RESUME_HTML, options=ExtractionOptions(use_cache=False, audit=False)
    )
    seen = backend.last_user_text() + " ".join(
        str(m.get("content", "")) for m in backend.calls[0]["messages"] if m.get("role") == "system"
    )
    assert "organize" in seen.lower()
    assert "resume" in seen.lower()  # example sections are hinted to the model


async def test_general_rejects_empty_then_succeeds_with_feedback(
    settings: Any, scripted_backend: Any
) -> None:
    """An empty analysis is invalid; the retry with feedback succeeds."""
    backend = scripted_backend(
        [
            '{"document_type": "resume", "sections": []}',  # invalid: no sections
            valid_general_document(),
        ]
    )
    agent = ExtractionAgent(settings, backend=backend)
    result = await agent.extract_html_text(
        RESUME_HTML, options=ExtractionOptions(use_cache=False, audit=False)
    )
    assert isinstance(result.data, StructuredDocument)
    assert result.provenance.attempt_count == 2
    feedback = backend.calls[1]["messages"][-1]["content"]
    assert "sections" in str(feedback)


async def test_general_merge_concatenates_sections(
    settings: Any, scripted_backend: Any
) -> None:
    """Multi-page PDFs: sections concatenate across pages, then revalidate."""
    doc1 = StructuredDocument(
        document_type="report",
        sections=[Section(title="Findings", bullets=["a"])],
    )
    doc2 = StructuredDocument(
        document_type="report",
        title="Annual Report",
        sections=[Section(title="Findings", bullets=["b"]), Section(title="Budget")],
    )
    agent = ExtractionAgent(settings, backend=scripted_backend([]))
    merged = agent.merge_instances([doc1, doc2], schema=StructuredDocument)
    assert isinstance(merged, StructuredDocument)
    assert merged.title == "Annual Report"  # first non-null scalar wins
    findings = [s for s in merged.sections if s.title == "Findings"]
    assert len(findings) == 2  # both pages' sections survive


async def test_general_schema_also_works_via_explicit_name(
    settings: Any, scripted_backend: Any
) -> None:
    """schema_name='general' resolves to the same model."""
    backend = scripted_backend([valid_general_document()])
    agent = ExtractionAgent(settings, backend=backend)
    result = await agent.extract_html_text(
        PRODUCT_HTML, schema_name="general", options=ExtractionOptions(use_cache=False, audit=False)
    )
    assert isinstance(result.data, StructuredDocument)
    assert result.provenance.schema_name == "general"
