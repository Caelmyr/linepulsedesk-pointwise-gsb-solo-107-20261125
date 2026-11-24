"""Alternative module name for monotone row alignment."""

from .correspondence import (  # noqa: F401
    DEFAULT_LIMIT,
    counterpart,
    normalize_adoption,
    read_limit,
    suggest,
)

align = suggest
align_lines = suggest
row_alignment = suggest
