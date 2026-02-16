"""Prompt templates for long-running agent sessions."""

from __future__ import annotations

from textwrap import dedent

from .feature_list import Feature


def build_initializer_prompt(
    app_spec: str,
    feature_target: int,
    *,
    app_spec_path: str = "app_spec.txt",
    feature_list_path: str = "feature_list.json",
    progress_path: str = "claude-progress.txt",
    init_script_path: str = "init.sh",
) -> str:
    """Create the initializer-session prompt."""
    return dedent(
        f"""
        ## ROLE: INITIALIZER AGENT (Session 1)

        You are the first agent in a long-running autonomous coding loop.
        Your job is to create durable project state so future sessions can continue reliably.

        ### Requirements
        1. Read `{app_spec_path}` and align implementation to it.
        2. Create `{feature_list_path}` with at least {feature_target} end-to-end test cases.
        3. Every feature must include:
           - `category` (functional/style/performance/reliability)
           - `description`
           - `steps` (array of concrete verification actions)
           - `passes` (boolean, start as false)
        4. Create `{init_script_path}` that boots the project for later sessions.
        5. Create or update README with local run instructions.
        6. If git is initialized, make a commit describing initialization work.
        7. Update `{progress_path}` with a concise summary of this session.

        ### Critical invariant
        Future sessions may only modify the `passes` field in `{feature_list_path}`.
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
    *,
    app_spec_path: str = "app_spec.txt",
    feature_list_path: str = "feature_list.json",
    progress_path: str = "claude-progress.txt",
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
        - cat {app_spec_path}
        - head -n 80 {feature_list_path}
        - tail -n 80 {progress_path} (if exists)
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
        4. Update only `passes` for verified features in `{feature_list_path}`.
        5. Never mutate feature descriptions, steps, or ordering.
        6. Update `{progress_path}` with what changed, verification results, and blockers.
        7. Keep the working tree clean at session end where possible.

        ### App spec reminder
        {app_spec}
        """
    ).strip()
