"""CLI: run, serve, schema-new, golden harness, registry listing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from extractor.config import Settings
from extractor.errors import ExtractionFailed, ExtractorError
from extractor.pipeline import ExtractionAgent, ExtractionOptions
from extractor.schemas.registry import SchemaRegistry

app = typer.Typer(
    name="extractor",
    help="Deterministic structured-data extraction from HTML and PDFs.",
    no_args_is_help=True,
    add_completion=False,
)


def _load_schemas(settings: Settings) -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register_user_dir(settings.user_schema_dir)
    return registry


def _agent(settings: Settings) -> ExtractionAgent:
    return ExtractionAgent(settings, registry=_load_schemas(settings))


def _resolve_schema(
    registry: SchemaRegistry, schema_name: str, schema_file: Path | None
) -> str:
    if schema_file is not None:
        registry.register_file(schema_file)
    names = registry.names()
    if not schema_name:
        if len(names) == 1:
            return names[0]
        raise typer.BadParameter(
            f"--schema required (registered: {', '.join(names) or 'none'})"
        )
    if schema_name not in names and schema_file is None:
        registry.get(schema_name)  # raises SchemaNotFound with available names
    return schema_name


@app.command()
def run(
    source: str = typer.Argument(..., help="URL, or path to an .html/.htm/.pdf file"),
    schema: str = typer.Option("", "--schema", "-s", help="Registered schema name"),
    schema_file: Path | None = typer.Option(
        None, "--schema-file", help="Path to a .py file defining the schema"
    ),
    model: str = typer.Option("", "--model", "-m", help="LiteLLM model string override"),
    backend: str = typer.Option("", "--backend", "-b", help="instructor | outlines"),
    temperature: float | None = typer.Option(None, help="Sampling temperature"),
    seed: int | None = typer.Option(None, help="Sampling seed"),
    max_attempts: int = typer.Option(0, help="Override max attempts"),
    max_escalations: int = typer.Option(0, help="Override max escalations"),
    no_cache: bool = typer.Option(False, help="Bypass the deterministic cache"),
    no_audit: bool = typer.Option(False, help="Do not write JSONL audit trails"),
    pretty: bool = typer.Option(True, "--pretty/--compact", help="Pretty-print JSON"),
) -> None:
    """Extract structured data from a URL, HTML file, or PDF file."""
    settings = Settings()
    registry = _load_schemas(settings)
    agent = ExtractionAgent(settings, registry=registry)
    name = _resolve_schema(registry, schema, schema_file)
    opts = ExtractionOptions(
        model=model,
        backend=backend,
        temperature=temperature,
        seed=seed,
        use_default_seed=seed is None,
        max_attempts=max_attempts,
        max_escalations=max_escalations,
        use_cache=not no_cache,
        audit=not no_audit,
    )
    try:
        if source.startswith(("http://", "https://")):
            result = asyncio.run(
                agent.extract_url(source, schema_name=name, options=opts)
            )
        elif source.lower().endswith(".pdf"):
            result = asyncio.run(
                agent.extract_pdf_file(Path(source), schema_name=name, options=opts)
            )
        else:
            result = asyncio.run(
                agent.extract_html_text(
                    Path(source).read_text(encoding="utf-8"),
                    origin=source,
                    schema_name=name,
                    options=opts,
                )
            )
    except ExtractionFailed as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        if exc.run_trace is not None:
            typer.secho(exc.audit_summary(), err=True)
        raise typer.Exit(code=1) from exc
    except ExtractorError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    payload = {
        "data": result.data.model_dump(mode="json"),
        "provenance": result.provenance.model_dump(mode="json"),
    }
    typer.echo(json.dumps(payload, indent=2 if pretty else None, sort_keys=True))
    typer.secho(
        f"[{result.provenance.run_id}] attempts={result.provenance.attempt_count} "
        f"cost=${result.run_trace.total_cost_usd:.6f}",
        fg=typer.colors.CYAN,
        err=True,
    )


@app.command()
def schemas() -> None:
    """List registered schemas."""
    registry = _load_schemas(Settings())
    for name in registry.names():
        schema = registry.get(name)
        typer.echo(f"{name:20s} {schema.schema_description[:70]}")


@app.command("schema-new")
def schema_new(
    name: str = typer.Argument(..., help="snake_case entity name, e.g. job_posting"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing file"),
) -> None:
    """Create a starter schema file in the user schema directory."""
    from extractor.schemas.template import render_template

    settings = Settings()
    target = settings.user_schema_dir / f"{name}.py"
    if target.exists() and not force:
        typer.secho(f"already exists: {target} (use --force)", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_template(name), encoding="utf-8")
    typer.echo(f"created {target}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
) -> None:
    """Run the HTTP extraction API."""
    import uvicorn

    uvicorn.run("extractor.api.main:app", host=host, port=port)


@app.command()
def golden(
    directory: Path = typer.Argument(..., exists=True, file_okay=False),
    schema: str = typer.Option("", "--schema"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    update: bool = typer.Option(False, "--update", help="Rewrite golden files"),
    no_llm: bool = typer.Option(
        True, "--llm/--no-llm", help="--no-llm replays recorded outputs only"
    ),
) -> None:
    """Run the golden-file regression harness (see extractor.harness.golden)."""
    from extractor.harness.golden import run_golden_suite

    settings = Settings()
    registry = _load_schemas(settings)
    name = _resolve_schema(registry, schema, schema_file)
    failures = asyncio.run(
        run_golden_suite(
            directory,
            registry.get(name),
            settings=settings,
            update=update,
            replay_only=no_llm,
        )
    )
    raise typer.Exit(code=1 if failures else 0)


@app.callback()
def main() -> None:
    """Deterministic structured-data extraction agent."""


if __name__ == "__main__":
    app()
