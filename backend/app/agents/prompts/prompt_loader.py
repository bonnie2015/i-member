from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=128)
def load_prompt(relative_path: str) -> str:
    
    prompt_path = Path(__file__).parent.parent / "prompts" / relative_path
    return prompt_path.read_text(encoding="utf-8")
