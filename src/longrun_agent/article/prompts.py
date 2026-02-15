"""Prompt loading helpers for article-mode harness."""

from __future__ import annotations

import shutil
from pathlib import Path


PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text()


def get_initializer_prompt() -> str:
    return load_prompt("initializer_prompt")


def get_coding_prompt() -> str:
    return load_prompt("coding_prompt")


def copy_spec_to_project(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    source = PROMPTS_DIR / "app_spec.txt"
    destination = project_dir / "app_spec.txt"
    if not destination.exists():
        shutil.copy(source, destination)
