"""Prompt templates for long-running agent sessions."""

from __future__ import annotations

from textwrap import dedent

from .feature_list import Feature


def build_initializer_prompt(app_spec: str, feature_target: int) -> str:
    """Create the initializer-session prompt."""
    return dedent(
        f"""
        ## ROLE: INITIALIZER AGENT (Session 1)

        You are the first agent in a long-running autonomous coding loop.
        Your job is to create durable project state so future sessions can continue reliably.

        ### Requirements
        1. Read app_spec.txt and align implementation to it.
        2. Create `feature_list.json` with at least {feature_target} end-to-end test cases.
        3. Every feature must include:
           - `category` (functional/style/performance/reliability)
           - `description`
           - `steps` (array of concrete verification actions)
           - `passes` (boolean, start as false)
        4. Create `init.sh` that boots the project for later sessions.
        5. Create or update README with local run instructions.
        6. If git is initialized, make a commit describing initialization work.

        ### Critical invariant
        Future sessions may only modify the `passes` field in feature_list.json.
        They must never edit descriptions, steps, or ordering.

        ### Application spec
        {app_spec}

        End this session in a clean, resumable state.
        """
    ).strip()


def build_coding_prompt(
    app_spec: str,
    feature_index: int,
    feature: Feature,
    passing: int,
    total: int,
) -> str:
    """Create the coding-session prompt."""
    steps = "\n".join(f"- {step}" for step in feature.get("steps", []))
    return dedent(
        f"""
        ## ROLE: CODING AGENT (Continuation Session)

        This is a fresh context window. Recover state from files before changing code.

        ### Mandatory bearings
        Run and inspect:
        - pwd
        - ls -la
        - cat app_spec.txt
        - head -n 80 feature_list.json
        - tail -n 80 claude-progress.txt (if exists)
        - git log --oneline -20 (if git repo)

        ### Current progress
        - Passing tests: {passing}/{total}
        - Next target feature index: {feature_index}
        - Category: {feature.get("category", "functional")}
        - Description: {feature.get("description", "")}

        ### Test steps for this feature
        {steps}

        ### Execution rules
        1. Fix regressions first if any existing passing feature is broken.
        2. Implement this single target feature end-to-end.
        3. Verify through real user-facing behavior (not just backend calls).
        4. Update only `passes` for verified features in feature_list.json.
        5. Never mutate feature descriptions, steps, or ordering.
        6. Keep the working tree clean at session end where possible.

        ### App spec reminder
        {app_spec}
        """
    ).strip()
