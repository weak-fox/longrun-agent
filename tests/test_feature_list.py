import json

from longrun_agent.feature_list import (
    detect_forbidden_mutations,
    first_pending_feature,
    progress_counts,
)


def test_detect_forbidden_mutations_allows_passes_flip() -> None:
    before = [
        {
            "category": "functional",
            "description": "User can log in",
            "steps": ["Open login page", "Submit credentials"],
            "passes": False,
        }
    ]
    after = [
        {
            "category": "functional",
            "description": "User can log in",
            "steps": ["Open login page", "Submit credentials"],
            "passes": True,
        }
    ]

    issues = detect_forbidden_mutations(before, after)

    assert issues == []


def test_detect_forbidden_mutations_flags_description_changes() -> None:
    before = [
        {
            "category": "functional",
            "description": "User can log in",
            "steps": ["Open login page", "Submit credentials"],
            "passes": False,
        }
    ]
    after = [
        {
            "category": "functional",
            "description": "User can log in with SSO",
            "steps": ["Open login page", "Submit credentials"],
            "passes": True,
        }
    ]

    issues = detect_forbidden_mutations(before, after)

    assert any("description" in issue for issue in issues)


def test_first_pending_feature_returns_first_false_item() -> None:
    features = [
        {
            "category": "functional",
            "description": "A",
            "steps": ["1"],
            "passes": True,
        },
        {
            "category": "functional",
            "description": "B",
            "steps": ["1"],
            "passes": False,
        },
    ]

    index, feature = first_pending_feature(features)

    assert index == 1
    assert feature["description"] == "B"


def test_progress_counts() -> None:
    features = [
        {"category": "functional", "description": "A", "steps": ["1"], "passes": True},
        {"category": "functional", "description": "B", "steps": ["1"], "passes": False},
        {"category": "style", "description": "C", "steps": ["1"], "passes": True},
    ]

    assert progress_counts(features) == (2, 3)
