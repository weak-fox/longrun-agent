"""Progress helpers for article-mode harness."""

from __future__ import annotations

import json
from pathlib import Path


def count_passing_tests(project_dir: Path) -> tuple[int, int]:
    feature_file = project_dir / "feature_list.json"
    if not feature_file.exists():
        return 0, 0

    try:
        features = json.loads(feature_file.read_text())
    except json.JSONDecodeError:
        return 0, 0

    if not isinstance(features, list):
        return 0, 0

    total = len(features)
    passing = sum(1 for item in features if isinstance(item, dict) and item.get("passes") is True)
    return passing, total


def print_progress_summary(project_dir: Path) -> None:
    passing, total = count_passing_tests(project_dir)
    if total > 0:
        percent = (passing / total) * 100
        print(f"Progress: {passing}/{total} tests passing ({percent:.1f}%)")
    else:
        print("Progress: feature_list.json not yet created")


def print_session_header(session_num: int, initializer: bool) -> None:
    session_type = "INITIALIZER" if initializer else "CODING AGENT"
    print("\n" + "=" * 70)
    print(f"  SESSION {session_num}: {session_type}")
    print("=" * 70)
