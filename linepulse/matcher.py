"""Another plausible module name for the row matcher."""

from .correspondence import (  # noqa: F401
    DEFAULT_LIMIT,
    counterpart,
    match_lines,
    normalize_adoption,
    suggest,
)

align_rows = match_lines
matching = suggest
