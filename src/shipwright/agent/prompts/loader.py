"""System prompts live as Markdown next to this file, one per agent (`<name>.md`).

`{placeholders}` are filled with `str.format`; a literal brace is written `{{` / `}}`.
"""

from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str, **values: Any) -> str:
    """`<name>.md` with its placeholders filled (a missing value raises KeyError)."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").format(**values)
