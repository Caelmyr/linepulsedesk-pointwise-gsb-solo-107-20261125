"""Alternative module name for the content row matching feature."""

from .correspondence import (  # noqa: F401
    DEFAULT_LIMIT,
    align_rows,
    counterpart,
    match_lines,
    normalize_adoption,
    read_limit,
    row_correspondence,
    suggest,
)


def match_rows(image_a, config_a, image_b, config_b, max_diff=DEFAULT_LIMIT, **kwargs):
    return suggest(image_a, config_a, image_b, config_b, limit=max_diff, **kwargs)
