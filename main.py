"""Vercel entrypoint: exposes the FastAPI app at a default-recognized location.

Vercel's Python runtime looks for ``app`` in main.py (among other default
locations) at the project root. This shim also guarantees the src-layout
package is importable even if the project wheel is not preinstalled in the
function image.
"""

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from extractor.api.main import app  # noqa: E402

__all__ = ["app"]
