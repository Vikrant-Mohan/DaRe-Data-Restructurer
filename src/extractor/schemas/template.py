"""Schema templating helper for ``extractor schema new``.

Renders a copy-paste-ready BaseExtraction subclass with strict types and a
cross-field validator, so users start from the intended shape.
"""

from __future__ import annotations

TEMPLATE = '''\
"""User extraction schema: {name}.

Edit the fields/validators, then run:
    extractor run <url-or-file> --schema {name}
"""

from __future__ import annotations

from pydantic import Field, model_validator

from extractor.schemas.base import BaseExtraction


class {class_name}(BaseExtraction):
    schema_name = "{name}"
    css_hints = ["main", "body"]  # optional: focus the DOM region
    schema_description = "Extract {name} entities from the document."

    name: str = Field(min_length=1)
    value: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "{class_name}":
        """Cross-field rules: error messages here become retry feedback."""
        return self
'''

ENTITY_RE = "^[a-z][a-z0-9_]*$"


def render_template(name: str) -> str:
    class_name = name.replace("_", " ").title().replace(" ", "")
    return TEMPLATE.format(name=name, class_name=class_name)
