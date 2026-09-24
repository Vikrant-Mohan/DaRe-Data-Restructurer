"""General-purpose "auto-analyze" schema: works on any document, no domain model.

``general`` produces a self-describing :class:`StructuredDocument`: the LLM
identifies the document type, invents logical section names (e.g. a resume
becomes Summary / Skills / Experience / Projects / Education), and organizes
key points under each section as bullet points or summary rows. Validation is
intentionally lenient — this schema is for understanding unknown documents,
not for enforcing a contract.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from extractor.schemas.base import BaseExtraction


class SummaryRow(BaseModel):
    """One label/value pair (e.g. Total: $170.00, Degree: B.Tech CSE)."""

    model_config = ConfigDict(extra="ignore")

    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


class Section(BaseModel):
    """A titled group of key points from the document."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1)
    summary: str | None = None
    bullets: list[str] = Field(default_factory=list)
    rows: list[SummaryRow] = Field(default_factory=list)


class StructuredDocument(BaseExtraction):
    """Schema-agnostic analysis of any document.

    The model picks the section titles to fit the content; the pipeline's
    validation-feedback loop still applies (e.g. an empty analysis is rejected
    and fed back), but there is no domain contract to violate.
    """

    schema_name = "general"
    css_hints: ClassVar[list[str]] = ["main", "article", "body"]
    # Multi-page PDFs: concatenate each page's sections, then revalidate.
    merge_strategy: ClassVar[str] = "concatenate_items"
    schema_description = (
        "Analyze this document without any preset schema: identify what it is, then "
        "organize its key content into clearly titled sections (choose names that fit "
        "the document — e.g. for a resume: Summary, Skills, Experience, Projects, "
        "Education; for an invoice: Parties, Line Items, Totals). Put the important "
        "facts of each section into concise bullet points (and label/value rows where "
        "a table fits better). Do not invent facts that are not in the document."
    )

    document_type: str = Field(
        min_length=1,
        description="Short label for what the document is, e.g. 'resume', 'invoice', 'product page'.",
    )
    title: str | None = Field(
        default=None, description="The document's own title or heading, if any."
    )
    sections: list[Section] = Field(
        min_length=1,
        description="The organized content: titled sections with bullets and/or label/value rows.",
    )
