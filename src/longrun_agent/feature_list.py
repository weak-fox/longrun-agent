"""Utilities for working with feature_list.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


Feature = dict[str, Any]
REQUIRED_KEYS = ("category", "description", "steps", "passes")


def load_feature_list(path: Path) -> list[Feature]:
    """Load and validate feature_list.json from disk."""
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        msg = "feature_list.json must contain a JSON array"
        raise ValueError(msg)

    issues = validate_feature_schema(data)
    if issues:
        joined = "\n".join(issues)
        raise ValueError(f"Invalid feature_list.json:\n{joined}")

    return data


def save_feature_list(path: Path, features: list[Feature]) -> None:
    """Write feature list in stable, readable JSON format."""
    path.write_text(json.dumps(features, indent=2, ensure_ascii=False) + "\n")


def validate_feature_schema(features: list[Feature]) -> list[str]:
    """Validate the baseline schema for each feature item."""
    issues: list[str] = []

    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            issues.append(f"feature #{index} must be an object")
            continue

        missing = [key for key in REQUIRED_KEYS if key not in feature]
        if missing:
            issues.append(f"feature #{index} missing required keys: {', '.join(missing)}")
            continue

        if not isinstance(feature["category"], str) or not feature["category"].strip():
            issues.append(f"feature #{index} has invalid category")

        if not isinstance(feature["description"], str) or not feature["description"].strip():
            issues.append(f"feature #{index} has invalid description")

        if not isinstance(feature["steps"], list) or not feature["steps"]:
            issues.append(f"feature #{index} must include at least one step")
        elif not all(isinstance(step, str) and step.strip() for step in feature["steps"]):
            issues.append(f"feature #{index} contains empty or invalid step values")

        if not isinstance(feature["passes"], bool):
            issues.append(f"feature #{index} has non-boolean passes value")

    return issues


def detect_forbidden_mutations(before: list[Feature], after: list[Feature]) -> list[str]:
    """
    Ensure immutable fields are not changed between sessions.

    The only allowed mutation is changing `passes` values.
    """
    issues: list[str] = []

    if len(before) != len(after):
        issues.append(
            "feature list length changed; adding/removing/reordering features is forbidden"
        )
        return issues

    for index, (old_feature, new_feature) in enumerate(zip(before, after, strict=True)):
        if not isinstance(old_feature, dict) or not isinstance(new_feature, dict):
            issues.append(f"feature #{index} is not a valid object")
            continue

        old_static = {k: v for k, v in old_feature.items() if k != "passes"}
        new_static = {k: v for k, v in new_feature.items() if k != "passes"}

        if old_static != new_static:
            changed_fields = sorted(
                key
                for key in set(old_static) | set(new_static)
                if old_static.get(key) != new_static.get(key)
            )
            field_list = ", ".join(changed_fields)
            issues.append(f"feature #{index} changed immutable fields: {field_list}")

        if not isinstance(new_feature.get("passes"), bool):
            issues.append(f"feature #{index} has non-boolean passes value")

    return issues


def first_pending_feature(features: list[Feature]) -> tuple[int, Feature]:
    """Return the first feature with passes=false."""
    for index, feature in enumerate(features):
        if feature.get("passes") is False:
            return index, feature

    raise ValueError("All features are passing")


def progress_counts(features: list[Feature]) -> tuple[int, int]:
    """Return (passing, total)."""
    total = len(features)
    passing = sum(1 for feature in features if feature.get("passes") is True)
    return passing, total
