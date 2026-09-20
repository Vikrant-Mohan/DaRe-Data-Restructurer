"""Schema registry: discover, load, validate and hash extraction schemas.

Schemas live in Python modules and are loaded either from the built-in
examples, from the user schema directory (``EXTRACT_USER_SCHEMA_DIR``), or
from an explicit ``module:ClassName`` reference.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import TypeGuard

from extractor.errors import SchemaInvalid, SchemaNotFound
from extractor.schemas.base import BaseExtraction


def _is_extraction_model(obj: object) -> TypeGuard[type[BaseExtraction]]:
    return (
        inspect.isclass(obj)
        and issubclass(obj, BaseExtraction)
        and obj is not BaseExtraction
        and bool(obj.schema_name)
    )


class SchemaRegistry:
    """Loads and indexes extraction schema classes by ``schema_name``."""

    def __init__(self) -> None:
        self._schemas: dict[str, type[BaseExtraction]] = {}
        self.register_builtin_examples()

    # ── registration ────────────────────────────────────────────────────────
    def register_builtin_examples(self) -> None:
        from extractor.schemas import examples

        self.register_module(examples)

    def register_module(self, module: object) -> list[str]:
        """Register every BaseExtraction subclass with a schema_name in a module."""
        names = []
        for _, obj in inspect.getmembers(module, _is_extraction_model):
            self.register(obj)
            names.append(obj.schema_name)
        return names

    def register(self, schema: type[BaseExtraction]) -> None:
        if not schema.schema_name:
            raise SchemaInvalid(f"{schema.__name__} must set schema_name")
        self._schemas[schema.schema_name] = schema

    def register_file(self, path: Path) -> list[str]:
        """Import a Python file and register the schemas found in it."""
        if not path.exists():
            raise SchemaNotFound(f"schema file not found: {path}")
        mod_name = f"extractor_user_schemas_{path.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise SchemaInvalid(f"cannot import schema file: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # surface user-code errors clearly
            raise SchemaInvalid(f"error importing {path}: {exc}") from exc
        return self.register_module(module)

    def register_user_dir(self, directory: Path) -> list[str]:
        """Register every ``*.py`` (except ``__*``) in a directory."""
        names: list[str] = []
        if not directory.exists():
            return names
        for path in sorted(directory.glob("*.py")):
            if path.stem.startswith("_"):
                continue
            names.extend(self.register_file(path))
        return names

    def register_reference(self, reference: str) -> list[str]:
        """Register from ``module.path:ClassName`` (or a whole module)."""
        module_path, _, attr = reference.partition(":")
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise SchemaInvalid(f"cannot import schema module '{module_path}': {exc}") from exc
        if attr:
            obj = getattr(module, attr, None)
            if obj is None:
                raise SchemaInvalid(f"{module_path} has no attribute {attr}")
            if not _is_extraction_model(obj):
                raise SchemaInvalid(f"{reference} is not a BaseExtraction subclass")
            self.register(obj)
            return [obj.schema_name]
        return self.register_module(module)

    # ── lookup ──────────────────────────────────────────────────────────────
    def get(self, name: str) -> type[BaseExtraction]:
        schema = self._schemas.get(name)
        if schema is None:
            available = ", ".join(sorted(self._schemas)) or "<none>"
            raise SchemaNotFound(
                f"schema '{name}' not registered (available: {available})"
            )
        return schema

    def names(self) -> list[str]:
        return sorted(self._schemas)
