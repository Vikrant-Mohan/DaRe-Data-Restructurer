"""Version markers that participate in cache keys and audit records.

Bump ``PROMPT_VERSION`` whenever the prompt templates change in a way that
could alter model output; the cache key incorporates it so stale entries are
never served.
"""

PROMPT_VERSION = "1.0.0"
SCHEMA_FORMAT_VERSION = "1.0.0"
__version__ = "0.1.0"
