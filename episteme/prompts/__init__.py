"""Re-export all prompt templates."""

from episteme.prompts.templates import *  # noqa: F401,F403
from episteme.prompts.extraction import (
    CASE_PROFILE,
    SOURCE_THESIS,
    EXTRACTOR_V4,
    GENERICITY_CHECK,
)
from episteme.prompts.methodology import (
    METHODOLOGY_PROFILE,
    METHODOLOGY_AUDIT,
)
