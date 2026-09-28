from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str


def load_prompt(name: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise PromptError(f"prompt not found: {name}")
    raw = path.read_text(encoding="utf-8")
    parts = raw.split("\n", 2)
    if len(parts) < 3 or not parts[0].startswith("version:") or parts[1] != "":
        raise PromptError(f"prompt {name} is missing a 'version: N' header")
    header, _, text = parts
    try:
        version = int(header.removeprefix("version:").strip())
    except ValueError:
        raise PromptError(f"prompt {name} has an invalid version header") from None
    return Prompt(name=name, version=version, text=text)
