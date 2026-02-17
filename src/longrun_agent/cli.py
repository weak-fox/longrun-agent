"""Command-line interface for long-running agent harness."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Sequence

from .backends.factory import create_backend
from .ci_pipeline import PullRequestContext, plan_pipeline_for_pr
from .config import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CODEX_COMMAND_TEMPLATE,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CONFIG_FILENAME,
    LEGACY_AGENT_PLACEHOLDER_COMMAND,
    LEGACY_CODEX_PLACEHOLDER_COMMAND,
    load_config,
    resolve_artifacts_dir,
    save_config,
    write_default_config,
)
from .harness import Harness
from .runtime.contracts import AgentRunRequest
from .runtime.context_guidance import (
    build_instruction_and_layered_reading_guidance,
    instruction_file_for_backend,
)
from .improvement_cycle import (
    SOURCE_TYPE_CHOICES,
    ImprovementTargets,
    add_research_bundle,
    add_research_evidence,
    build_cycle_payload,
    build_diagnosis,
    build_experiment_plans,
    build_hypotheses,
    enforce_research_requirement,
    evidence_file_path,
    evaluate_budget_gate,
    load_research_evidence,
    load_cycle_memory,
    memory_file_path,
    record_cycle_memory,
    save_cycle_memory,
    render_cycle_markdown,
    select_research_claims_for_diagnosis,
)
from .self_improve import analyze_recent_sessions, build_recommendations, render_plan_markdown

BACKEND_CHOICES = ("codex_cli", "claude_sdk")
PROFILE_CHOICES = ("default", "article")


@dataclass(slots=True)
class GuidedGoalDraft:
    goal: str
    primary_users: str
    core_flows: list[str]
    constraints: list[str]
    done_criteria: str
    feature_target: int
    assumptions: list[str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="longrun-agent",
        description="Harness for long-running autonomous coding agents",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(DEFAULT_CONFIG_FILENAME),
        help=f"Path to config TOML (default: {DEFAULT_CONFIG_FILENAME})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="Create starter config and baseline files")
    bootstrap.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Project directory where harness state should live",
    )
    bootstrap.add_argument(
        "--guided",
        action="store_true",
        help="Run an interactive goal setup wizard for app_spec.txt",
    )

    run_session = subparsers.add_parser("run-session", help="Run one initializer/coding session")
    run_session.add_argument(
        "--backend",
        type=str,
        choices=BACKEND_CHOICES,
        default=None,
        help="Override runtime backend",
    )
    run_session.add_argument(
        "--profile",
        type=str,
        choices=PROFILE_CHOICES,
        default=None,
        help="Override prompt profile",
    )
    run_session.add_argument(
        "--backend-model",
        type=str,
        default=None,
        help="Override backend model (applies to model-aware backends)",
    )
    run_session.add_argument(
        "--model-reasoning-effort",
        type=str,
        default=None,
        help="Override model reasoning effort (codex_cli only)",
    )

    run_loop = subparsers.add_parser("run-loop", help="Run repeated sessions")
    run_loop.add_argument(
        "--max-sessions",
        type=int,
        default=None,
        help="Maximum number of sessions to run before exiting",
    )
    run_loop.add_argument(
        "--backend",
        type=str,
        choices=BACKEND_CHOICES,
        default=None,
        help="Override runtime backend",
    )
    run_loop.add_argument(
        "--profile",
        type=str,
        choices=PROFILE_CHOICES,
        default=None,
        help="Override prompt profile",
    )
    run_loop.add_argument(
        "--backend-model",
        type=str,
        default=None,
        help="Override backend model (applies to model-aware backends)",
    )
    run_loop.add_argument(
        "--model-reasoning-effort",
        type=str,
        default=None,
        help="Override model reasoning effort (codex_cli only)",
    )
    run_loop.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Do not stop loop on failed sessions; continue until max-sessions is reached",
    )

    go = subparsers.add_parser(
        "go",
        help="Guided one-command workflow: clarify goal, configure defaults, then run loop",
    )
    go.add_argument(
        "--goal",
        type=str,
        default=None,
        help="Product goal in one sentence",
    )
    go.add_argument(
        "--max-sessions",
        type=int,
        default=20,
        help="Maximum number of sessions to run (default: 20)",
    )
    go.add_argument(
        "--continue-on-failure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Continue loop when a session fails (default: true)",
    )
    go.add_argument(
        "--backend",
        type=str,
        choices=BACKEND_CHOICES,
        default=None,
        help="Override runtime backend",
    )
    go.add_argument(
        "--profile",
        type=str,
        choices=PROFILE_CHOICES,
        default=None,
        help="Override prompt profile",
    )
    go.add_argument(
        "--backend-model",
        type=str,
        default=None,
        help="Override backend model",
    )
    go.add_argument(
        "--model-reasoning-effort",
        type=str,
        default=None,
        help="Override model reasoning effort (codex_cli only)",
    )
    go.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Override harness project directory",
    )
    go.add_argument(
        "--feature-target",
        type=int,
        default=None,
        help="Override initializer feature target",
    )
    go.add_argument(
        "--brainstorm-rounds",
        type=int,
        default=2,
        help="How many agent-generated clarification questions to ask",
    )
    go.add_argument(
        "--skip-brainstorm",
        action="store_true",
        help="Skip agent clarification questions before drafting app_spec",
    )
    go.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not ask interactive questions; rely on defaults and passed flags",
    )
    go.add_argument(
        "--yes",
        action="store_true",
        help="Auto-accept suggested draft values in interactive mode",
    )
    go.add_argument(
        "--allow-any-python",
        action="store_true",
        help="Skip .venv-longrun environment check (advanced)",
    )

    status = subparsers.add_parser("status", help="Show harness status summary")
    status.add_argument(
        "--json",
        action="store_true",
        help="Print status as JSON",
    )

    self_improve = subparsers.add_parser(
        "self-improve",
        help="Analyze recent sessions and propose/apply safe self-improvement steps",
    )
    self_improve.add_argument(
        "--window",
        type=int,
        default=20,
        help="How many recent sessions to analyze (default: 20)",
    )
    self_improve.add_argument(
        "--apply",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply safe automatic tuning when recommended (default: true)",
    )

    def _add_improvement_cycle_args(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--window",
            type=int,
            default=20,
            help="How many recent sessions to analyze (default: 20)",
        )
        command_parser.add_argument(
            "--max-failure-rate",
            type=float,
            default=0.10,
            help="Budget target for failure_rate (default: 0.10)",
        )
        command_parser.add_argument(
            "--max-no-progress-rate",
            type=float,
            default=0.25,
            help="Budget target for no_progress_rate (default: 0.25)",
        )
        command_parser.add_argument(
            "--min-sessions",
            type=int,
            default=10,
            help="Minimum sessions required before budget decision (default: 10)",
        )
        command_parser.add_argument(
            "--auto-bootstrap",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Automatically run sampling sessions when session window is below min-sessions "
                "(default: true)"
            ),
        )
        command_parser.add_argument(
            "--bootstrap-sessions",
            type=int,
            default=None,
            help=(
                "Max sessions to run for automatic sampling; default is min-sessions gap"
            ),
        )
        command_parser.add_argument(
            "--auto-research",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Automatically run topic-based research when evidence is insufficient (default: true)",
        )
        command_parser.add_argument(
            "--topic",
            type=str,
            default=None,
            help="Research topic override for auto research in improvement-cycle",
        )
        command_parser.add_argument(
            "--enforce-budget",
            action="store_true",
            help="Exit non-zero when budget gate status is not promote",
        )
        command_parser.add_argument(
            "--json",
            action="store_true",
            help="Print cycle payload as JSON",
        )

    improvement_cycle = subparsers.add_parser(
        "improvement-cycle",
        help="Run control-plane improvement cycle (diagnose, plan, budget-gate, evaluate)",
    )
    _add_improvement_cycle_args(improvement_cycle)

    run_cycle = subparsers.add_parser(
        "run-cycle",
        help="Alias for improvement-cycle with the same full automation behavior",
    )
    _add_improvement_cycle_args(run_cycle)

    improvement_research = subparsers.add_parser(
        "improvement-research",
        help="Manage local research evidence base used by improvement-cycle",
    )
    improvement_research.add_argument(
        "--list",
        action="store_true",
        help="List current evidence sources and claims",
    )
    improvement_research.add_argument(
        "--topic",
        type=str,
        default=None,
        help="Research topic for automatic evidence discovery (web + repo context)",
    )
    improvement_research.add_argument(
        "--max-sources",
        type=int,
        default=6,
        help="Maximum sources to collect in auto research mode (default: 6)",
    )
    improvement_research.add_argument(
        "--max-claims",
        type=int,
        default=12,
        help="Maximum claims to collect in auto research mode (default: 12)",
    )
    improvement_research.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="Stable source id for adding/updating a research source",
    )
    improvement_research.add_argument(
        "--title",
        type=str,
        default=None,
        help="Source title when adding evidence",
    )
    improvement_research.add_argument(
        "--url",
        type=str,
        default=None,
        help="Source URL when adding evidence",
    )
    improvement_research.add_argument(
        "--source-type",
        type=str,
        choices=SOURCE_TYPE_CHOICES,
        default="community",
        help="Source type classification (official/vendor/community)",
    )
    improvement_research.add_argument(
        "--claim",
        action="append",
        default=None,
        help="Evidence claim statement; repeatable",
    )
    improvement_research.add_argument(
        "--tags",
        type=str,
        default="",
        help="Comma-separated tags for source and claims",
    )
    improvement_research.add_argument(
        "--notes",
        type=str,
        default="",
        help="Optional source rationale/notes",
    )

    simulate_pr = subparsers.add_parser(
        "simulate-pr",
        help="Simulate CI stage scheduling for a PR update",
    )
    simulate_pr.add_argument(
        "--repository-language",
        type=str,
        default="go",
        help="Repository primary language (default: go)",
    )
    simulate_pr.add_argument(
        "--source-branch",
        type=str,
        required=True,
        help="PR source branch name (e.g. feature/my-change)",
    )
    simulate_pr.add_argument(
        "--target-branch",
        type=str,
        required=True,
        help="PR target branch name (e.g. develop or main)",
    )
    simulate_pr.add_argument(
        "--commit-sha",
        type=str,
        required=True,
        help="Commit SHA included in the PR update",
    )

    configure = subparsers.add_parser(
        "configure",
        help="Interactive setup/update for longrun-agent.toml",
    )
    configure.add_argument(
        "--backend",
        type=str,
        choices=BACKEND_CHOICES,
        default=None,
        help="Set runtime backend",
    )
    configure.add_argument(
        "--profile",
        type=str,
        choices=PROFILE_CHOICES,
        default=None,
        help="Set prompt profile",
    )
    configure.add_argument(
        "--backend-model",
        type=str,
        default=None,
        help="Set runtime backend model",
    )
    configure.add_argument(
        "--model-reasoning-effort",
        type=str,
        default=None,
        help="Set runtime model reasoning effort (codex_cli only)",
    )
    configure.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Set harness project directory",
    )
    configure.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Set harness state directory (defaults to <project_dir>/.longrun when unset)",
    )
    configure.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="Set generated artifact directory (defaults to <resolved state_dir>/artifacts when unset)",
    )
    configure.add_argument(
        "--codex-command",
        type=str,
        default=None,
        help="Set codex command template, shell-style string",
    )
    configure.add_argument(
        "--codex-timeout-seconds",
        type=int,
        default=None,
        help="Set codex backend timeout in seconds",
    )
    configure.add_argument(
        "--commit-required",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Toggle commit-required gate",
    )
    configure.add_argument(
        "--progress-update-required",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Toggle progress-update-required gate",
    )
    configure.add_argument(
        "--repair-on-verification-failure",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Toggle repair-on-verification-failure gate",
    )
    configure.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip prompts and only apply explicitly passed options",
    )

    return parser


def _prompt_int(label: str, current: int) -> int:
    while True:
        value = input(f"{label} [{current}]: ").strip()
        if not value:
            return current
        try:
            parsed = int(value)
        except ValueError:
            print("Please enter a valid integer.")
            continue
        if parsed <= 0:
            print("Please enter a positive integer.")
            continue
        return parsed


def _split_csv_values(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",")]
    return [item for item in values if item]


def _resolve_artifacts_dir(config) -> Path:
    return resolve_artifacts_dir(config.project_dir, config.state_dir, config.artifacts_dir)


def _render_guided_app_spec(
    goal: str,
    primary_users: str,
    core_flows: list[str],
    constraints: list[str],
    done_criteria: str,
    assumptions: list[str] | None = None,
) -> str:
    flow_lines = "\n".join(f"- {item}" for item in core_flows) if core_flows else "- TBD"
    constraint_lines = "\n".join(f"- {item}" for item in constraints) if constraints else "- None"
    assumptions_lines = "\n".join(f"- {item}" for item in (assumptions or [])) or "- None"
    return dedent(
        f"""\
        # Application Spec

        ## Product Goal
        {goal or "TBD"}

        ## Primary Users
        {primary_users or "TBD"}

        ## Core Flows
        {flow_lines}

        ## Constraints
        {constraint_lines}

        ## Definition of Done
        {done_criteria or "TBD"}

        ## Assumptions
        {assumptions_lines}
        """
    )


def _extract_json_object(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty agent output")

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    if "```json" in stripped:
        start = stripped.find("```json")
        end = stripped.find("```", start + 7)
        if end != -1:
            candidate = stripped[start + 7 : end].strip()
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed

    first = stripped.find("{")
    last = stripped.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise ValueError("no JSON object found in agent output")
    candidate = stripped[first : last + 1]
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("agent JSON output is not an object")
    return parsed


def _build_improvement_research_prompt(
    *,
    topic: str,
    max_sources: int,
    max_claims: int,
    backend_name: str,
) -> str:
    instruction_file = instruction_file_for_backend(backend_name)
    guidance = build_instruction_and_layered_reading_guidance(backend_name)
    return dedent(
        f"""\
        You are a research analyst for improving an autonomous coding harness.
        Do focused web research and return evidence for this topic:

        {topic}

        Before research:
        - Read `{instruction_file}` first if present.
        - Use layered repo reading to anchor research in this repository's actual constraints.
        {guidance}

        Research requirements:
        - Prefer high-signal sources: official docs/standards, vendor engineering docs, high-quality community references.
        - Avoid duplicate sources and duplicate claims.
        - Each claim must be actionable for process/architecture decisions in this repo.

        Return ONLY valid JSON (no markdown, no code fences) with shape:
        {{
          "sources": [
            {{
              "title": "string",
              "url": "https://...",
              "source_type": "official|vendor|community",
              "rationale": "why this source is relevant",
              "tags": ["string"]
            }}
          ],
          "claims": [
            {{
              "source_url": "https://...",
              "statement": "single actionable claim",
              "tags": ["string"]
            }}
          ]
        }}

        Limits:
        - sources: at most {max_sources}
        - claims: at most {max_claims}
        """
    ).strip()


def _parse_improvement_research_bundle(
    raw_output: str,
    *,
    max_sources: int,
    max_claims: int,
    default_source_type: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    data = _extract_json_object(raw_output)
    raw_sources = data.get("sources", [])
    raw_claims = data.get("claims", [])

    sources: list[dict[str, object]] = []
    if isinstance(raw_sources, list):
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", item.get("name", ""))).strip()
            url = str(item.get("url", "")).strip()
            if not title or not url:
                continue
            source_type = str(item.get("source_type", default_source_type)).strip().lower()
            if source_type not in SOURCE_TYPE_CHOICES:
                source_type = default_source_type
            tags = item.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            sources.append(
                {
                    "title": title,
                    "url": url,
                    "source_type": source_type,
                    "rationale": str(item.get("rationale", "")).strip(),
                    "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
                }
            )
    sources = sources[:max_sources]

    source_urls = {str(item["url"]) for item in sources}
    claims: list[dict[str, object]] = []
    if isinstance(raw_claims, list):
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            source_url = str(item.get("source_url", "")).strip()
            statement = str(item.get("statement", "")).strip()
            if not source_url or not statement:
                continue
            if source_urls and source_url not in source_urls:
                continue
            tags = item.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            claims.append(
                {
                    "source_url": source_url,
                    "statement": statement,
                    "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
                }
            )
    claims = claims[:max_claims]
    return sources, claims


def _run_auto_improvement_research(
    *,
    config,
    topic: str,
    max_sources: int,
    max_claims: int,
    source_type: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    research_dir = config.project_dir / ".longrun" / "improvement-research"
    research_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = research_dir / "auto-research.prompt.md"
    prompt_file.write_text(
        _build_improvement_research_prompt(
            topic=topic,
            max_sources=max_sources,
            max_claims=max_claims,
            backend_name=config.backend_name,
        )
    )

    backend = create_backend(
        backend_name=config.backend_name,
        project_dir=config.project_dir,
        command_template=config.agent_command,
        model=config.backend_model,
    )
    run_result = backend.run(
        AgentRunRequest(
            phase="initializer",
            project_dir=config.project_dir,
            prompt_file=prompt_file,
            session_dir=research_dir,
            timeout_seconds=min(config.agent_timeout_seconds, 900),
            backend_model=config.backend_model,
            model_reasoning_effort=config.model_reasoning_effort,
        )
    )
    if run_result.timeout:
        raise RuntimeError("auto research timed out")
    if run_result.return_code not in {0, None}:
        stderr = run_result.stderr_path.read_text().strip() if run_result.stderr_path.exists() else ""
        raise RuntimeError(f"auto research failed (code={run_result.return_code}): {stderr}")
    if not run_result.stdout_path.exists():
        raise RuntimeError("auto research produced no stdout output")

    return _parse_improvement_research_bundle(
        run_result.stdout_path.read_text(),
        max_sources=max_sources,
        max_claims=max_claims,
        default_source_type=source_type,
    )


def _normalize_text_list(value: object, *, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result or fallback


def _normalize_goal_draft_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def _get_goal_draft_value(
    data: dict,
    normalized_data: dict[str, object],
    keys: Sequence[str],
    default: object,
) -> object:
    for key in keys:
        if key in data:
            return data[key]
        normalized_key = _normalize_goal_draft_key(key)
        if normalized_key in normalized_data:
            return normalized_data[normalized_key]
    return default


def _parse_goal_draft(raw_output: str, goal: str, default_feature_target: int) -> GuidedGoalDraft:
    data = _extract_json_object(raw_output)
    normalized_data = {_normalize_goal_draft_key(key): value for key, value in data.items()}

    primary_users = str(
        _get_goal_draft_value(
            data,
            normalized_data,
            keys=("primary_users", "users", "primary users"),
            default="Individuals and small teams",
        )
    ).strip()
    if not primary_users:
        primary_users = "Individuals and small teams"

    core_flows = _normalize_text_list(
        _get_goal_draft_value(
            data,
            normalized_data,
            keys=("core_flows", "flows", "core flows"),
            default=None,
        ),
        fallback=["Create item", "Edit item", "Complete item", "Search item"],
    )
    constraints = _normalize_text_list(
        _get_goal_draft_value(
            data,
            normalized_data,
            keys=("constraints",),
            default=None,
        ),
        fallback=["Reuse existing stack", "Keep dependencies minimal"],
    )
    assumptions = _normalize_text_list(
        _get_goal_draft_value(
            data,
            normalized_data,
            keys=("assumptions",),
            default=None,
        ),
        fallback=[],
    )

    done_criteria = str(
        _get_goal_draft_value(
            data,
            normalized_data,
            keys=(
                "done_criteria",
                "done criteria",
                "definition_of_done",
                "definition of done",
            ),
            default="A new user can complete the core flow, and verification commands pass",
        )
    ).strip()
    if not done_criteria:
        done_criteria = "A new user can complete the core flow, and verification commands pass"

    target_raw = _get_goal_draft_value(
        data,
        normalized_data,
        keys=("feature_target", "feature target"),
        default=default_feature_target,
    )
    try:
        feature_target = int(target_raw)
    except (TypeError, ValueError):
        feature_target = default_feature_target
    feature_target = max(1, min(feature_target, 500))

    return GuidedGoalDraft(
        goal=goal,
        primary_users=primary_users,
        core_flows=core_flows,
        constraints=constraints,
        done_criteria=done_criteria,
        feature_target=feature_target,
        assumptions=assumptions,
    )


def _build_goal_expansion_prompt(goal: str, backend_name: str) -> str:
    instruction_file = instruction_file_for_backend(backend_name)
    guidance = build_instruction_and_layered_reading_guidance(backend_name)
    return dedent(
        f"""\
        You are a product requirements analyst.
        Expand this one-sentence product goal into a practical implementation draft that
        fits the existing repository instead of inventing a greenfield rewrite.

        Product goal:
        {goal}

        Before writing the JSON output, recover repository context:
        - Read `{instruction_file}` first (if present).
        - Execute layered reads and ground your output in what already exists.
        {guidance}

        Return ONLY valid JSON (no markdown, no code fences) with this shape:
        {{
          "primary_users": "string",
          "core_flows": ["string", "string", "string", "string"],
          "constraints": ["string", "string"],
          "done_criteria": "string",
          "feature_target": 40,
          "assumptions": ["string"]
        }}

        Rules:
        - Use the same language as the product goal.
        - core_flows: 4-8 items, concrete and user-visible.
        - constraints: 2-6 items, realistic for a first version and aligned with existing stack.
        - feature_target: integer in [20, 80].
        - Keep assumptions explicit if requirements are ambiguous.
        - Prefer incremental improvements over technology migration unless the goal explicitly asks for migration.
        """
    ).strip()


def _build_goal_question_prompt(
    goal: str,
    history: list[tuple[str, str]],
    backend_name: str,
) -> str:
    history_lines = (
        "\n".join(f"- Q: {question}\n  A: {answer}" for question, answer in history)
        if history
        else "- None"
    )
    instruction_file = instruction_file_for_backend(backend_name)
    guidance = build_instruction_and_layered_reading_guidance(backend_name)
    return dedent(
        f"""\
        You are helping refine a software product goal before implementation.
        Ask exactly one high-value clarification question.

        Product goal:
        {goal}

        Before asking the question:
        - Read `{instruction_file}` first (if present).
        - Use layered repository reading to find the biggest ambiguity that affects implementation risk.
        {guidance}

        Existing clarification history:
        {history_lines}

        Return ONLY valid JSON (no markdown, no code fences):
        {{
          "question": "string"
        }}

        Rules:
        - Use the same language as the product goal.
        - Ask one concrete question that reduces implementation ambiguity.
        - Keep it short and specific.
        """
    ).strip()


def _parse_goal_question(raw_output: str) -> str:
    try:
        data = _extract_json_object(raw_output)
        question = str(data.get("question", "")).strip()
        if question:
            return question
    except Exception:
        pass

    for line in raw_output.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if stripped:
            return stripped
    raise ValueError("agent question output is empty")


def _generate_goal_question_with_agent(
    config,
    goal: str,
    history: list[tuple[str, str]],
) -> str:
    temp_dir = config.project_dir / ".longrun" / "guided-goal"
    temp_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = temp_dir / "goal-question.prompt.md"
    prompt_file.write_text(
        _build_goal_question_prompt(
            goal,
            history,
            backend_name=config.backend_name,
        )
    )

    backend = create_backend(
        backend_name=config.backend_name,
        project_dir=config.project_dir,
        command_template=config.agent_command,
        model=config.backend_model,
    )
    run_result = backend.run(
        AgentRunRequest(
            phase="initializer",
            project_dir=config.project_dir,
            prompt_file=prompt_file,
            session_dir=temp_dir,
            timeout_seconds=min(config.agent_timeout_seconds, 600),
            backend_model=config.backend_model,
        )
    )
    if run_result.timeout:
        raise RuntimeError("agent brainstorming question timed out")
    if run_result.return_code not in {0, None}:
        stderr = run_result.stderr_path.read_text().strip() if run_result.stderr_path.exists() else ""
        raise RuntimeError(f"agent brainstorming question failed (code={run_result.return_code}): {stderr}")
    if not run_result.stdout_path.exists():
        raise RuntimeError("agent brainstorming question produced no stdout output")

    return _parse_goal_question(run_result.stdout_path.read_text())


def _collect_goal_clarifications(
    config,
    goal: str,
    rounds: int,
) -> list[tuple[str, str]]:
    if rounds <= 0:
        return []

    history: list[tuple[str, str]] = []
    for index in range(rounds):
        question = _generate_goal_question_with_agent(config, goal, history)
        print("")
        print(f"Brainstorm question {index + 1}: {question}")
        answer = input("Your answer (leave blank to stop): ").strip()
        if not answer:
            break
        history.append((question, answer))
    return history


def _merge_goal_with_clarifications(goal: str, clarifications: list[tuple[str, str]]) -> str:
    if not clarifications:
        return goal
    clarification_lines = "\n".join(
        f"- Q: {question}\n  A: {answer}" for question, answer in clarifications
    )
    return f"{goal}\n\nClarifications:\n{clarification_lines}"


def _fallback_goal_draft(config, goal: str, reason: str) -> GuidedGoalDraft:
    assumptions = [f"Draft fallback reason: {reason}"] if reason else []
    return GuidedGoalDraft(
        goal=goal,
        primary_users="Individuals and small teams",
        core_flows=["Create item", "Edit item", "Complete item", "Search item"],
        constraints=["Reuse existing stack", "Keep dependencies minimal"],
        done_criteria="A new user can complete the core flow, and verification commands pass",
        feature_target=max(20, min(config.feature_target, 80)),
        assumptions=assumptions,
    )


def _generate_goal_draft_with_agent(config, goal: str) -> GuidedGoalDraft:
    temp_dir = config.project_dir / ".longrun" / "guided-goal"
    temp_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = temp_dir / "goal-draft.prompt.md"
    prompt_file.write_text(
        _build_goal_expansion_prompt(
            goal,
            backend_name=config.backend_name,
        )
    )

    backend = create_backend(
        backend_name=config.backend_name,
        project_dir=config.project_dir,
        command_template=config.agent_command,
        model=config.backend_model,
    )
    run_result = backend.run(
        AgentRunRequest(
            phase="initializer",
            project_dir=config.project_dir,
            prompt_file=prompt_file,
            session_dir=temp_dir,
            timeout_seconds=min(config.agent_timeout_seconds, 900),
            backend_model=config.backend_model,
        )
    )
    if run_result.timeout:
        raise RuntimeError("agent draft generation timed out")
    if run_result.return_code not in {0, None}:
        stderr = run_result.stderr_path.read_text().strip() if run_result.stderr_path.exists() else ""
        raise RuntimeError(f"agent draft generation failed (code={run_result.return_code}): {stderr}")
    if not run_result.stdout_path.exists():
        raise RuntimeError("agent draft generation produced no stdout output")

    raw_output = run_result.stdout_path.read_text()
    return _parse_goal_draft(
        raw_output=raw_output,
        goal=goal,
        default_feature_target=max(20, min(config.feature_target, 80)),
    )


def _print_goal_draft(draft: GuidedGoalDraft) -> None:
    print("")
    print("Agent-generated draft:")
    print(f"- Primary users: {draft.primary_users}")
    print("- Core flows:")
    for flow in draft.core_flows:
        print(f"  - {flow}")
    print("- Constraints:")
    for constraint in draft.constraints:
        print(f"  - {constraint}")
    print(f"- Definition of done: {draft.done_criteria}")
    if draft.assumptions:
        print("- Assumptions:")
        for assumption in draft.assumptions:
            print(f"  - {assumption}")
    print(f"- Suggested feature_target: {draft.feature_target}")


def _run_manual_goal_wizard(config, goal: str) -> GuidedGoalDraft:
    print("Falling back to manual draft because agent drafting is unavailable.")
    primary_users = _prompt_text(
        "Primary users (e.g. solo users, small teams, operators)",
        "Individuals and small teams",
    )
    flows_raw = _prompt_text(
        "Core flows (comma-separated, e.g. create task, update task, search task)",
        "Create item, edit item, complete item, search item",
    )
    constraints_raw = _prompt_text(
        "Constraints (comma-separated, optional; e.g. local-first, no paid APIs)",
        "Reuse existing stack, keep dependencies minimal",
    )
    done_criteria = _prompt_text(
        "Definition of done (how to judge success)",
        "A new user can complete the core flow, and verification commands pass",
    )
    assumptions_raw = _prompt_text(
        "Assumptions (comma-separated, optional)",
        "",
    )
    return GuidedGoalDraft(
        goal=goal,
        primary_users=primary_users,
        core_flows=_split_csv_values(flows_raw),
        constraints=_split_csv_values(constraints_raw),
        done_criteria=done_criteria,
        feature_target=max(20, min(config.feature_target, 80)),
        assumptions=_split_csv_values(assumptions_raw),
    )


def _run_goal_wizard(config) -> None:
    print("Goal setup wizard (agent-assisted).")
    print("You only need one sentence; the agent drafts the rest.")
    goal = _prompt_text(
        "Product goal (one sentence, e.g. 做一个给小团队用的任务看板)",
        "Build a practical web app that solves one clear workflow end-to-end",
    )

    try:
        draft = _generate_goal_draft_with_agent(config, goal)
    except Exception as exc:
        print(f"Agent draft generation failed: {exc}")
        draft = _run_manual_goal_wizard(config, goal)

    _print_goal_draft(draft)

    if not _prompt_bool("Use this draft", True):
        correction = _prompt_text(
            "Add one correction sentence for regeneration",
            "",
        )
        refined_goal = f"{goal}\nCorrection: {correction}".strip()
        try:
            draft = _generate_goal_draft_with_agent(config, refined_goal)
            _print_goal_draft(draft)
        except Exception as exc:
            print(f"Regeneration failed, keeping previous draft: {exc}")

    config.feature_target = _prompt_int(
        "Initializer feature target (20-80 recommended for first run)",
        draft.feature_target,
    )
    artifacts_dir = _resolve_artifacts_dir(config)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    spec_path = artifacts_dir / "app_spec.txt"
    spec_path.write_text(
        _render_guided_app_spec(
            goal=draft.goal,
            primary_users=draft.primary_users,
            core_flows=draft.core_flows,
            constraints=draft.constraints,
            done_criteria=draft.done_criteria,
            assumptions=draft.assumptions,
        )
    )
    print(f"Updated app spec: {spec_path}")
    print("You can edit app_spec.txt manually any time before run-session.")


def _run_goal_setup_for_go(
    *,
    config,
    goal: str,
    interactive: bool,
    brainstorm_rounds: int,
    skip_brainstorm: bool,
    assume_yes: bool,
    feature_target_override: int | None,
) -> None:
    clarified_goal = goal.strip()
    clarifications: list[tuple[str, str]] = []
    if interactive and not skip_brainstorm and brainstorm_rounds > 0:
        try:
            clarifications = _collect_goal_clarifications(
                config=config,
                goal=clarified_goal,
                rounds=brainstorm_rounds,
            )
        except Exception as exc:
            print(f"Brainstorm question generation failed: {exc}")

    draft_goal_input = _merge_goal_with_clarifications(clarified_goal, clarifications)

    try:
        generated = _generate_goal_draft_with_agent(config, draft_goal_input)
        draft = GuidedGoalDraft(
            goal=clarified_goal,
            primary_users=generated.primary_users,
            core_flows=generated.core_flows,
            constraints=generated.constraints,
            done_criteria=generated.done_criteria,
            feature_target=generated.feature_target,
            assumptions=generated.assumptions,
        )
    except Exception as exc:
        print(f"Agent draft generation failed: {exc}")
        if interactive:
            draft = _run_manual_goal_wizard(config, clarified_goal)
        else:
            draft = _fallback_goal_draft(config, clarified_goal, str(exc))

    _print_goal_draft(draft)

    if interactive and not assume_yes and not _prompt_bool("Use this draft", True):
        correction = _prompt_text("Add one correction sentence for regeneration", "")
        refined_goal_input = f"{draft_goal_input}\nCorrection: {correction}".strip()
        try:
            regenerated = _generate_goal_draft_with_agent(config, refined_goal_input)
            draft = GuidedGoalDraft(
                goal=clarified_goal,
                primary_users=regenerated.primary_users,
                core_flows=regenerated.core_flows,
                constraints=regenerated.constraints,
                done_criteria=regenerated.done_criteria,
                feature_target=regenerated.feature_target,
                assumptions=regenerated.assumptions,
            )
            _print_goal_draft(draft)
        except Exception as exc:
            print(f"Regeneration failed, keeping previous draft: {exc}")

    if feature_target_override is not None:
        config.feature_target = feature_target_override
    elif interactive and not assume_yes:
        config.feature_target = _prompt_int(
            "Initializer feature target (20-80 recommended for first run)",
            draft.feature_target,
        )
    else:
        config.feature_target = draft.feature_target

    artifacts_dir = _resolve_artifacts_dir(config)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    spec_path = artifacts_dir / "app_spec.txt"
    spec_path.write_text(
        _render_guided_app_spec(
            goal=draft.goal,
            primary_users=draft.primary_users,
            core_flows=draft.core_flows,
            constraints=draft.constraints,
            done_criteria=draft.done_criteria,
            assumptions=draft.assumptions,
        )
    )
    print(f"Updated app spec: {spec_path}")


def _extract_product_goal_from_spec_text(spec_text: str) -> str | None:
    lines = spec_text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() != "## product goal":
            continue
        for candidate in lines[index + 1 :]:
            value = candidate.strip()
            if not value:
                continue
            if value in {"TBD", "- TBD"}:
                return None
            return value
        return None
    return None


def _has_meaningful_goal_in_spec(spec_path: Path) -> bool:
    if not spec_path.exists():
        return False
    text = spec_path.read_text().strip()
    if not text:
        return False
    goal = _extract_product_goal_from_spec_text(text)
    if goal:
        return True
    return "Describe the product you want the agent to build." not in text


def _validate_project_python_environment(
    project_dir: Path,
    allow_any_python: bool,
) -> str | None:
    if allow_any_python:
        return None

    expected_venv = (project_dir / ".venv-longrun").resolve()
    if not expected_venv.exists():
        return (
            "Project virtual environment not found.\n"
            f"- expected: {expected_venv}\n"
            f"- create with: python3 -m venv {expected_venv}"
        )

    current_prefix = Path(sys.prefix).resolve()
    active_virtual_env = os.environ.get("VIRTUAL_ENV")
    active_virtual_env_path = Path(active_virtual_env).resolve() if active_virtual_env else None

    if current_prefix == expected_venv or active_virtual_env_path == expected_venv:
        return None

    activate_script = expected_venv / "bin" / "activate"
    return (
        "Current Python environment does not match project .venv-longrun.\n"
        f"- expected: {expected_venv}\n"
        f"- current: {active_virtual_env or current_prefix}\n"
        f"- fix: source {activate_script}"
    )


def _default_state_dir_for_project(project_dir: Path) -> Path:
    return project_dir.resolve() / ".longrun"


def _resolve_config_path(config_path: Path, project_dir_hint: Path | None = None) -> Path:
    del project_dir_hint
    default_path = Path(DEFAULT_CONFIG_FILENAME)
    if config_path != default_path:
        return config_path
    return default_path


def _run_first_time_setup_for_go(config_path: Path, project_dir: Path | None = None) -> int:
    print("First-time setup (required).")

    current_project_dir = project_dir.resolve() if project_dir is not None else Path.cwd().resolve()
    backend = _prompt_text("Backend (codex_cli/claude_sdk)", "codex_cli")
    profile = _prompt_text("Profile (default/article)", "default")
    default_model = DEFAULT_CLAUDE_MODEL if backend == "claude_sdk" else DEFAULT_CODEX_MODEL
    backend_model = _prompt_text("Backend model", default_model)
    configured_project_dir = Path(
        _prompt_text("Project dir", current_project_dir.as_posix())
    ).resolve()
    state_dir = Path(
        _prompt_text(
            "State dir (separate runtime state directory)",
            _default_state_dir_for_project(configured_project_dir).as_posix(),
        )
    ).resolve()
    commit_required = _prompt_bool("Require commit each session", False)
    progress_update_required = _prompt_bool("Require progress update each session", False)
    repair_on_verification_failure = _prompt_bool("Attempt repair on verification failure", True)

    state_dir.mkdir(parents=True, exist_ok=True)
    return run_configure(
        config_path=config_path,
        backend=backend,
        profile=profile,
        backend_model=backend_model,
        project_dir=configured_project_dir,
        state_dir=state_dir,
        commit_required=commit_required,
        progress_update_required=progress_update_required,
        repair_on_verification_failure=repair_on_verification_failure,
        non_interactive=True,
    )


def _sync_backend_defaults(config, *, backend_was_set: bool, backend_model_was_set: bool) -> None:
    if config.backend_name == "codex_cli":
        if backend_was_set and not backend_model_was_set and config.backend_model == DEFAULT_CLAUDE_MODEL:
            config.backend_model = DEFAULT_CODEX_MODEL
        if config.agent_command in (
            LEGACY_AGENT_PLACEHOLDER_COMMAND,
            LEGACY_CODEX_PLACEHOLDER_COMMAND,
        ):
            config.agent_command = list(DEFAULT_CODEX_COMMAND_TEMPLATE)
    elif config.backend_name == "claude_sdk":
        if backend_was_set and not backend_model_was_set and config.backend_model == DEFAULT_CODEX_MODEL:
            config.backend_model = DEFAULT_CLAUDE_MODEL


def _apply_model_to_codex_command(command: list[str], model: str) -> list[str]:
    updated = list(command)
    if any("{backend_model}" in token for token in updated):
        return updated

    for flag in ("-m", "--model"):
        if flag in updated:
            index = updated.index(flag)
            if index + 1 < len(updated):
                updated[index + 1] = model
                return updated

    if len(updated) >= 2 and updated[0] == "codex" and updated[1] == "exec":
        return ["codex", "exec", "-m", model, *updated[2:]]

    return updated


def run_bootstrap(config_path: Path, project_dir: Path | None, guided: bool = False) -> int:
    default_project_dir = project_dir.resolve() if project_dir is not None else None
    write_default_config(config_path, project_dir=default_project_dir)
    config = load_config(config_path)
    config.project_dir = (
        project_dir.resolve() if project_dir is not None else config.project_dir.resolve()
    )
    Harness(config).bootstrap()
    if guided:
        if not sys.stdin.isatty():
            print("bootstrap --guided requires an interactive terminal", file=sys.stderr)
            return 2
        _run_goal_wizard(config)
    save_config(config_path, config)

    print(f"Wrote config template: {config_path}")
    print(f"Prepared harness files in: {config.project_dir}")
    print("Edit longrun-agent.toml runtime/backend settings before running sessions.")
    return 0


def _apply_runtime_overrides(
    config,
    backend: str | None = None,
    profile: str | None = None,
    backend_model: str | None = None,
    model_reasoning_effort: str | None = None,
) -> None:
    backend_was_set = backend is not None
    backend_model_was_set = backend_model is not None
    if backend is not None:
        config.backend_name = backend
    if profile is not None:
        config.profile = profile
    if backend_model is not None:
        config.backend_model = backend_model
    if model_reasoning_effort is not None:
        trimmed_effort = model_reasoning_effort.strip()
        config.model_reasoning_effort = trimmed_effort or None
    _sync_backend_defaults(
        config,
        backend_was_set=backend_was_set,
        backend_model_was_set=backend_model_was_set,
    )
    if config.backend_name == "codex_cli" and backend_model_was_set:
        config.agent_command = _apply_model_to_codex_command(config.agent_command, config.backend_model)


def run_one_session(
    config_path: Path,
    backend: str | None = None,
    profile: str | None = None,
    backend_model: str | None = None,
    model_reasoning_effort: str | None = None,
) -> int:
    config = load_config(config_path)
    _apply_runtime_overrides(
        config,
        backend=backend,
        profile=profile,
        backend_model=backend_model,
        model_reasoning_effort=model_reasoning_effort,
    )
    harness = Harness(config)
    result = harness.run_session()
    print(
        f"session={result.session_id:04d} phase={result.phase} "
        f"success={result.success} progress={result.passing}/{result.total} "
        f"message={result.message}"
    )
    return 0 if result.success else 1


def run_loop(
    config_path: Path,
    max_sessions: int | None,
    continue_on_failure: bool = False,
    backend: str | None = None,
    profile: str | None = None,
    backend_model: str | None = None,
    model_reasoning_effort: str | None = None,
) -> int:
    if continue_on_failure and max_sessions is None:
        print(
            "--continue-on-failure requires --max-sessions to avoid unbounded retries",
            file=sys.stderr,
        )
        return 2

    config = load_config(config_path)
    _apply_runtime_overrides(
        config,
        backend=backend,
        profile=profile,
        backend_model=backend_model,
        model_reasoning_effort=model_reasoning_effort,
    )
    harness = Harness(config)
    results = harness.run_loop(
        max_sessions=max_sessions,
        continue_on_failure=continue_on_failure,
    )

    for result in results:
        print(
            f"session={result.session_id:04d} phase={result.phase} "
            f"success={result.success} progress={result.passing}/{result.total} "
            f"message={result.message}"
        )

    if not results:
        return 0

    if harness.last_loop_stop_reason:
        print(harness.last_loop_stop_reason)
        return 1

    last = results[-1]
    if not last.success:
        return 1

    if last.total > 0 and last.passing == last.total:
        print("All features passing. Loop stopped cleanly.")

    return 0


def run_go(
    config_path: Path,
    goal: str | None = None,
    max_sessions: int | None = 20,
    continue_on_failure: bool = True,
    backend: str | None = None,
    profile: str | None = None,
    backend_model: str | None = None,
    model_reasoning_effort: str | None = None,
    project_dir: Path | None = None,
    feature_target: int | None = None,
    brainstorm_rounds: int = 2,
    skip_brainstorm: bool = False,
    non_interactive: bool = False,
    yes: bool = False,
    allow_any_python: bool = False,
) -> int:
    if max_sessions is not None and max_sessions <= 0:
        print("--max-sessions must be a positive integer", file=sys.stderr)
        return 2
    if feature_target is not None and feature_target <= 0:
        print("--feature-target must be a positive integer", file=sys.stderr)
        return 2
    if brainstorm_rounds < 0:
        print("--brainstorm-rounds must be >= 0", file=sys.stderr)
        return 2

    interactive = not non_interactive and sys.stdin.isatty()
    resolved_project_dir = project_dir.resolve() if project_dir is not None else None
    config_exists = config_path.exists()
    if not config_exists and interactive:
        setup_code = _run_first_time_setup_for_go(config_path, project_dir)
        if setup_code != 0:
            return setup_code

    write_default_config(config_path, project_dir=resolved_project_dir)
    config = load_config(config_path)

    if resolved_project_dir is not None:
        config.project_dir = resolved_project_dir
    else:
        config.project_dir = config.project_dir.resolve()

    _apply_runtime_overrides(
        config,
        backend=backend,
        profile=profile,
        backend_model=backend_model,
        model_reasoning_effort=model_reasoning_effort,
    )

    if feature_target is not None:
        config.feature_target = feature_target

    env_error = _validate_project_python_environment(config.project_dir, allow_any_python)
    if env_error is not None:
        print(env_error, file=sys.stderr)
        return 2

    Harness(config).bootstrap()
    goal_value = (goal or "").strip()
    spec_path = _resolve_artifacts_dir(config) / "app_spec.txt"

    if not goal_value and interactive and not _has_meaningful_goal_in_spec(spec_path):
        goal_value = _prompt_text(
            "Product goal (one sentence, e.g. 做一个给小团队用的任务看板)",
            "Build a practical web app that solves one clear workflow end-to-end",
        )

    if goal_value:
        _run_goal_setup_for_go(
            config=config,
            goal=goal_value,
            interactive=interactive,
            brainstorm_rounds=brainstorm_rounds,
            skip_brainstorm=skip_brainstorm,
            assume_yes=yes,
            feature_target_override=feature_target,
        )
    elif not _has_meaningful_goal_in_spec(spec_path):
        print(
            "No concrete Product Goal found. Pass --goal or update app_spec.txt first.",
            file=sys.stderr,
        )
        return 2

    save_config(config_path, config)
    return run_loop(
        config_path,
        max_sessions,
        continue_on_failure=continue_on_failure,
        backend=backend,
        profile=profile,
        backend_model=backend_model,
        model_reasoning_effort=model_reasoning_effort,
    )


def run_status(config_path: Path, as_json: bool) -> int:
    config = load_config(config_path)
    harness = Harness(config)
    summary = harness.status_summary()

    if as_json:
        print(json.dumps(summary, indent=2))
        return 0

    progress = summary["progress"]
    print(f"project: {summary['project_dir']}")
    print(
        f"progress: {progress['passing']}/{progress['total']} "
        f"({progress['percentage']:.1f}%)"
    )
    print(f"sessions: {summary['session_count']}")

    if summary["next_pending"] is not None:
        pending = summary["next_pending"]
        print(
            f"next: #{pending['index']} [{pending['category']}] "
            f"{pending['description']}"
        )
    else:
        print("next: none (all passing or feature list missing)")

    if summary["lock"] is not None:
        lock = summary["lock"]
        pid = lock.get("pid")
        started = lock.get("started_at")
        print(f"lock: active pid={pid} started_at={started}")
    else:
        print("lock: none")

    return 0


def run_self_improve(
    *,
    config_path: Path,
    window: int,
    apply: bool,
) -> int:
    if window <= 0:
        print("--window must be a positive integer", file=sys.stderr)
        return 2

    config = load_config(config_path)
    harness = Harness(config)
    harness.bootstrap()

    report = analyze_recent_sessions(state_dir=harness.state_dir, window=window)
    recommendations = build_recommendations(
        report,
        repair_on_verification_failure=config.repair_on_verification_failure,
    )

    applied_actions: list[str] = []
    if apply:
        for recommendation in recommendations:
            if (
                recommendation.auto_apply
                and recommendation.config_field == "repair_on_verification_failure"
                and bool(recommendation.config_value) is True
                and not config.repair_on_verification_failure
            ):
                config.repair_on_verification_failure = True
                applied_actions.append(
                    "Set gates.repair_on_verification_failure = true "
                    "(triggered by verification_commands_pass failures)"
                )

    if applied_actions:
        save_config(config_path, config, preserve_unmanaged=True)

    plan_text = render_plan_markdown(
        report=report,
        recommendations=recommendations,
        applied_actions=applied_actions,
    )
    harness.artifacts_dir.mkdir(parents=True, exist_ok=True)
    plan_path = harness.artifacts_dir / "self-improvement-plan.md"
    plan_path.write_text(plan_text)

    print(f"sessions_analyzed={report.session_count} failures={report.failure_count}")
    print(f"recommendations={len(recommendations)} applied_actions={len(applied_actions)}")
    print(f"plan={plan_path}")
    return 0


def run_improvement_cycle(
    *,
    config_path: Path,
    window: int,
    max_failure_rate: float,
    max_no_progress_rate: float,
    min_sessions: int,
    auto_bootstrap: bool,
    bootstrap_sessions: int | None,
    auto_research: bool,
    topic: str | None,
    enforce_budget: bool,
    as_json: bool,
) -> int:
    if window <= 0:
        print("--window must be a positive integer", file=sys.stderr)
        return 2
    if min_sessions <= 0:
        print("--min-sessions must be a positive integer", file=sys.stderr)
        return 2
    if not (0.0 <= max_failure_rate <= 1.0):
        print("--max-failure-rate must be in [0, 1]", file=sys.stderr)
        return 2
    if not (0.0 <= max_no_progress_rate <= 1.0):
        print("--max-no-progress-rate must be in [0, 1]", file=sys.stderr)
        return 2
    if bootstrap_sessions is not None and bootstrap_sessions <= 0:
        print("--bootstrap-sessions must be a positive integer when set", file=sys.stderr)
        return 2

    config = load_config(config_path)
    harness = Harness(config)
    harness.bootstrap()

    report = analyze_recent_sessions(state_dir=harness.state_dir, window=window)
    orchestration: dict[str, Any] = {
        "auto_bootstrap": {
            "enabled": auto_bootstrap,
            "attempted": False,
            "reason": "",
            "requested_sessions": 0,
            "sampled_sessions": 0,
            "sampled_failures": 0,
            "error": "",
        },
        "auto_research": {
            "enabled": auto_research,
            "attempted": False,
            "topic": "",
            "sources_added_estimate": 0,
            "claims_added_estimate": 0,
            "error": "",
        },
    }

    needs_session_count_bootstrap = report.session_count < min_sessions
    needs_coding_signal_bootstrap = report.session_count > 0 and report.coding_sessions == 0
    if auto_bootstrap and (needs_session_count_bootstrap or needs_coding_signal_bootstrap):
        requested_default = max(min_sessions - report.session_count, 0)
        if needs_coding_signal_bootstrap:
            requested_default = max(requested_default, 3)
        requested = bootstrap_sessions if bootstrap_sessions is not None else requested_default
        requested = max(requested, 1)
        if requested > 0:
            orchestration["auto_bootstrap"]["attempted"] = True
            orchestration["auto_bootstrap"]["reason"] = (
                "insufficient_session_count"
                if needs_session_count_bootstrap
                else "no_coding_signal"
            )
            orchestration["auto_bootstrap"]["requested_sessions"] = requested
            try:
                sampling_config = load_config(config_path)
                sampling_config.auto_continue_delay_seconds = 0
                sampling_harness = Harness(sampling_config)
                sampling_harness.bootstrap()
                results = sampling_harness.run_loop(
                    max_sessions=requested,
                    continue_on_failure=True,
                )
                orchestration["auto_bootstrap"]["sampled_sessions"] = len(results)
                orchestration["auto_bootstrap"]["sampled_failures"] = sum(
                    1 for item in results if not item.success
                )
            except Exception as exc:
                orchestration["auto_bootstrap"]["error"] = str(exc)
        report = analyze_recent_sessions(state_dir=harness.state_dir, window=window)

    targets = ImprovementTargets(
        max_failure_rate=max_failure_rate,
        max_no_progress_rate=max_no_progress_rate,
        min_sessions=min_sessions,
    )
    diagnosis = build_diagnosis(report)
    research_path = evidence_file_path(harness.artifacts_dir)
    research_evidence = load_research_evidence(research_path, bootstrap_if_missing=True)
    memory_path = memory_file_path(harness.artifacts_dir)
    cycle_memory = load_cycle_memory(memory_path)

    topic_value = (topic or "").strip()
    if not topic_value:
        topic_value = (
            "How to improve autonomous coding harness reliability and reduce "
            "no-progress sessions with evidence-driven experiments"
        )

    if auto_research and not research_evidence.get("claims"):
        orchestration["auto_research"]["attempted"] = True
        orchestration["auto_research"]["topic"] = topic_value
        try:
            auto_sources, auto_claims = _run_auto_improvement_research(
                config=config,
                topic=topic_value,
                max_sources=6,
                max_claims=12,
                source_type="community",
            )
            research_evidence = add_research_bundle(
                path=research_path,
                sources=[dict(item) for item in auto_sources],
                claims=[dict(item) for item in auto_claims],
                default_source_type="community",
            )
            orchestration["auto_research"]["sources_added_estimate"] = len(auto_sources)
            orchestration["auto_research"]["claims_added_estimate"] = len(auto_claims)
        except Exception as exc:
            orchestration["auto_research"]["error"] = str(exc)

    selected_research_claims = select_research_claims_for_diagnosis(
        research_evidence,
        diagnosis,
        memory=cycle_memory,
    )
    if auto_research and not selected_research_claims:
        orchestration["auto_research"]["attempted"] = True
        orchestration["auto_research"]["topic"] = topic_value
        try:
            auto_sources, auto_claims = _run_auto_improvement_research(
                config=config,
                topic=topic_value,
                max_sources=6,
                max_claims=12,
                source_type="community",
            )
            research_evidence = add_research_bundle(
                path=research_path,
                sources=[dict(item) for item in auto_sources],
                claims=[dict(item) for item in auto_claims],
                default_source_type="community",
            )
            orchestration["auto_research"]["sources_added_estimate"] += len(auto_sources)
            orchestration["auto_research"]["claims_added_estimate"] += len(auto_claims)
            selected_research_claims = select_research_claims_for_diagnosis(
                research_evidence,
                diagnosis,
                memory=cycle_memory,
            )
        except Exception as exc:
            orchestration["auto_research"]["error"] = str(exc)

    budget_gate = evaluate_budget_gate(
        session_count=report.session_count,
        failure_count=report.failure_count,
        coding_sessions=report.coding_sessions,
        no_progress_sessions=report.no_progress_sessions,
        targets=targets,
    )
    budget_gate = enforce_research_requirement(
        budget_gate,
        selected_research_claims,
    )
    hypotheses = build_hypotheses(diagnosis, selected_research_claims)
    experiment_plans = build_experiment_plans(hypotheses, targets, selected_research_claims)
    payload = build_cycle_payload(
        report=report,
        targets=targets,
        diagnosis=diagnosis,
        budget_gate=budget_gate,
        hypotheses=hypotheses,
        experiment_plans=experiment_plans,
        research_evidence=research_evidence,
        selected_research_claims=selected_research_claims,
        cycle_memory=cycle_memory,
    )

    updated_memory = record_cycle_memory(
        cycle_memory,
        generated_at=str(payload.get("generated_at", "")),
        budget_gate_status=str(payload.get("budget_gate", {}).get("status", "")),
        primary_bottleneck=str(payload.get("diagnosis", {}).get("primary_bottleneck", "")),
        selected_claim_ids=[
            str(item.get("claim_id", "")).strip()
            for item in selected_research_claims
            if str(item.get("claim_id", "")).strip()
        ],
        hypothesis_ids=[
            str(item.get("hypothesis_id", "")).strip()
            for item in payload.get("hypotheses", [])
            if isinstance(item, dict) and str(item.get("hypothesis_id", "")).strip()
        ],
    )
    save_cycle_memory(memory_path, updated_memory)
    payload["memory"] = {
        "recent_cycles": updated_memory.get("cycles", [])[-5:],
        "claim_usage": updated_memory.get("claim_usage", {}),
    }
    payload["orchestration"] = orchestration

    harness.artifacts_dir.mkdir(parents=True, exist_ok=True)
    json_path = harness.artifacts_dir / "improvement-cycle.json"
    md_path = harness.artifacts_dir / "improvement-cycle.md"
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    md_path.write_text(render_cycle_markdown(payload))

    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"sessions_analyzed={report.session_count}")
        if orchestration["auto_bootstrap"]["attempted"]:
            print(
                "auto_bootstrap="
                f"sessions_requested:{orchestration['auto_bootstrap']['requested_sessions']} "
                f"sampled:{orchestration['auto_bootstrap']['sampled_sessions']} "
                f"failures:{orchestration['auto_bootstrap']['sampled_failures']}"
            )
        if orchestration["auto_research"]["attempted"]:
            print(
                "auto_research="
                f"sources_added~:{orchestration['auto_research']['sources_added_estimate']} "
                f"claims_added~:{orchestration['auto_research']['claims_added_estimate']}"
            )
        print(
            f"budget_gate={budget_gate.status} "
            f"failure_rate={budget_gate.failure_rate:.3f} "
            f"no_progress_rate={budget_gate.no_progress_rate:.3f}"
        )
        print(f"artifacts={json_path}, {md_path}")

    if enforce_budget and budget_gate.status != "promote":
        return 1
    return 0


def run_improvement_research(
    *,
    config_path: Path,
    list_only: bool,
    topic: str | None,
    max_sources: int,
    max_claims: int,
    source_id: str | None,
    title: str | None,
    url: str | None,
    source_type: str,
    claims: list[str] | None,
    tags: str,
    notes: str,
) -> int:
    if max_sources <= 0:
        print("--max-sources must be a positive integer", file=sys.stderr)
        return 2
    if max_claims <= 0:
        print("--max-claims must be a positive integer", file=sys.stderr)
        return 2

    config = load_config(config_path)
    harness = Harness(config)
    harness.bootstrap()

    research_path = evidence_file_path(harness.artifacts_dir)
    payload = load_research_evidence(research_path, bootstrap_if_missing=True)

    topic_value = (topic or "").strip()
    if topic_value:
        try:
            auto_sources, auto_claims = _run_auto_improvement_research(
                config=config,
                topic=topic_value,
                max_sources=max_sources,
                max_claims=max_claims,
                source_type=source_type,
            )
        except Exception as exc:
            print(f"auto research failed: {exc}", file=sys.stderr)
            return 1

        payload = add_research_bundle(
            path=research_path,
            sources=[dict(item) for item in auto_sources],
            claims=[dict(item) for item in auto_claims],
            default_source_type=source_type,
        )
        print(f"updated={research_path}")
        print(
            f"topic={topic_value} sources_added~={len(auto_sources)} claims_added~={len(auto_claims)}"
        )
        print(f"sources={len(payload.get('sources', []))} claims={len(payload.get('claims', []))}")
        return 0

    adding_requested = any(
        value is not None and str(value).strip()
        for value in (source_id, title, url)
    ) or bool(claims)
    if adding_requested:
        if not (source_id and title and url):
            print(
                "--source-id/--title/--url are required when adding evidence",
                file=sys.stderr,
            )
            return 2
        if not claims:
            print("--claim is required when adding evidence", file=sys.stderr)
            return 2
        payload = add_research_evidence(
            path=research_path,
            source_id=source_id,
            title=title,
            url=url,
            source_type=source_type,
            claims=claims,
            tags=[item for item in tags.split(",") if item.strip()],
            notes=notes,
        )

    if list_only or not adding_requested:
        print(f"evidence_path={research_path}")
        print(f"sources={len(payload.get('sources', []))} claims={len(payload.get('claims', []))}")
        for source in payload.get("sources", []):
            print(
                f"- {source.get('source_id')} ({source.get('source_type')}): "
                f"{source.get('name')} -> {source.get('url')}"
            )
    else:
        print(f"updated={research_path}")
        print(f"sources={len(payload.get('sources', []))} claims={len(payload.get('claims', []))}")

    return 0


def run_simulate_pr(
    *,
    repository_language: str,
    source_branch: str,
    target_branch: str,
    commit_sha: str,
) -> int:
    context = PullRequestContext(
        repository_language=repository_language,
        source_branch=source_branch,
        target_branch=target_branch,
        commit_sha=commit_sha,
    )
    plan = plan_pipeline_for_pr(context)

    print(
        "PR update:"
        f" {context.source_branch} -> {context.target_branch}"
        f" ({context.repository_language}) commit={context.commit_sha}"
    )
    for index, stage in enumerate(plan.stages, start=1):
        print(f"stage {index}: {stage.label}")
    return 0


def _prompt_text(label: str, current: str) -> str:
    value = input(f"{label} [{current}]: ").strip()
    return value or current


def _prompt_bool(label: str, current: bool) -> bool:
    current_text = "Y/n" if current else "y/N"
    value = input(f"{label} [{current_text}]: ").strip().lower()
    if not value:
        return current
    return value in {"y", "yes", "true", "1"}


def _parse_command_template(raw: str) -> list[str]:
    tokens = shlex.split(raw)
    if not tokens:
        raise ValueError("codex command cannot be empty")
    return tokens


def run_configure(
    config_path: Path,
    backend: str | None = None,
    profile: str | None = None,
    backend_model: str | None = None,
    model_reasoning_effort: str | None = None,
    project_dir: Path | None = None,
    state_dir: Path | None = None,
    artifacts_dir: Path | None = None,
    codex_command: str | None = None,
    codex_timeout_seconds: int | None = None,
    commit_required: bool | None = None,
    progress_update_required: bool | None = None,
    repair_on_verification_failure: bool | None = None,
    non_interactive: bool = False,
) -> int:
    write_default_config(config_path)
    config = load_config(config_path)

    interactive = (
        not non_interactive and backend is None and profile is None and sys.stdin.isatty()
    )
    if interactive:
        backend = _prompt_text("Backend (codex_cli/claude_sdk)", config.backend_name)
        profile = _prompt_text("Profile (default/article)", config.profile)
        backend_model = _prompt_text("Backend model", config.backend_model)
        effort_default = config.model_reasoning_effort or ""
        model_reasoning_effort = _prompt_text(
            "Model reasoning effort (codex_cli only, blank to unset)",
            effort_default,
        )
        project_dir = Path(_prompt_text("Project dir", config.project_dir.as_posix()))
        state_default = (
            config.state_dir.as_posix()
            if config.state_dir is not None
            else _default_state_dir_for_project(project_dir.resolve()).as_posix()
        )
        state_dir = Path(_prompt_text("State dir", state_default))
        artifacts_default = config.artifacts_dir.as_posix() if config.artifacts_dir is not None else ""
        artifacts_raw = _prompt_text(
            "Artifacts dir (relative to project_dir; blank means <state_dir>/artifacts)",
            artifacts_default,
        )
        artifacts_dir = Path(artifacts_raw) if artifacts_raw.strip() else None
        commit_required = _prompt_bool("Require commit each session", config.commit_required)
        progress_update_required = _prompt_bool(
            "Require progress update each session",
            config.progress_update_required,
        )
        repair_on_verification_failure = _prompt_bool(
            "Attempt repair on verification failure",
            config.repair_on_verification_failure,
        )
        codex_default = shlex.join(config.agent_command)
        codex_command = _prompt_text("Codex command template", codex_default)

    if backend is not None:
        config.backend_name = backend
    if profile is not None:
        config.profile = profile
    if backend_model is not None:
        config.backend_model = backend_model
    if model_reasoning_effort is not None:
        trimmed_effort = model_reasoning_effort.strip()
        config.model_reasoning_effort = trimmed_effort or None
    if project_dir is not None:
        config.project_dir = project_dir
    if state_dir is not None:
        config.state_dir = state_dir
    if artifacts_dir is not None:
        config.artifacts_dir = artifacts_dir
    if codex_command is not None:
        config.agent_command = _parse_command_template(codex_command)
    if codex_timeout_seconds is not None:
        config.agent_timeout_seconds = codex_timeout_seconds
    if commit_required is not None:
        config.commit_required = commit_required
    if progress_update_required is not None:
        config.progress_update_required = progress_update_required
    if repair_on_verification_failure is not None:
        config.repair_on_verification_failure = repair_on_verification_failure
    _sync_backend_defaults(
        config,
        backend_was_set=backend is not None,
        backend_model_was_set=backend_model is not None,
    )
    if config.backend_name == "codex_cli" and backend_model is not None:
        config.agent_command = _apply_model_to_codex_command(config.agent_command, config.backend_model)

    save_config(config_path, config, preserve_unmanaged=True)
    print(f"Updated config: {config_path}")
    print(
        f"backend={config.backend_name} profile={config.profile} "
        f"model={config.backend_model}"
    )
    print(f"model_reasoning_effort={config.model_reasoning_effort or '(unset)'}")
    print(f"project_dir={config.project_dir}")
    print(f"state_dir={config.state_dir or '(default: <project_dir>/.longrun)'}")
    print(f"artifacts_dir={config.artifacts_dir or '(default: <state_dir>/artifacts)'}")
    print(f"codex_command={shlex.join(config.agent_command)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_dir_hint = getattr(args, "project_dir", None)
    resolved_config_path = _resolve_config_path(args.config, project_dir_hint=project_dir_hint)

    if args.command == "bootstrap":
        return run_bootstrap(resolved_config_path, args.project_dir, guided=args.guided)

    if args.command == "run-session":
        return run_one_session(
            resolved_config_path,
            backend=args.backend,
            profile=args.profile,
            backend_model=args.backend_model,
            model_reasoning_effort=args.model_reasoning_effort,
        )

    if args.command == "run-loop":
        return run_loop(
            resolved_config_path,
            args.max_sessions,
            continue_on_failure=args.continue_on_failure,
            backend=args.backend,
            profile=args.profile,
            backend_model=args.backend_model,
            model_reasoning_effort=args.model_reasoning_effort,
        )

    if args.command == "go":
        return run_go(
            config_path=resolved_config_path,
            goal=args.goal,
            max_sessions=args.max_sessions,
            continue_on_failure=args.continue_on_failure,
            backend=args.backend,
            profile=args.profile,
            backend_model=args.backend_model,
            model_reasoning_effort=args.model_reasoning_effort,
            project_dir=args.project_dir,
            feature_target=args.feature_target,
            brainstorm_rounds=args.brainstorm_rounds,
            skip_brainstorm=args.skip_brainstorm,
            non_interactive=args.non_interactive,
            yes=args.yes,
            allow_any_python=args.allow_any_python,
        )

    if args.command == "status":
        return run_status(resolved_config_path, args.json)

    if args.command == "self-improve":
        return run_self_improve(
            config_path=resolved_config_path,
            window=args.window,
            apply=args.apply,
        )

    if args.command in {"improvement-cycle", "run-cycle"}:
        return run_improvement_cycle(
            config_path=resolved_config_path,
            window=args.window,
            max_failure_rate=args.max_failure_rate,
            max_no_progress_rate=args.max_no_progress_rate,
            min_sessions=args.min_sessions,
            auto_bootstrap=args.auto_bootstrap,
            bootstrap_sessions=args.bootstrap_sessions,
            auto_research=args.auto_research,
            topic=args.topic,
            enforce_budget=args.enforce_budget,
            as_json=args.json,
        )

    if args.command == "improvement-research":
        return run_improvement_research(
            config_path=resolved_config_path,
            list_only=args.list,
            topic=args.topic,
            max_sources=args.max_sources,
            max_claims=args.max_claims,
            source_id=args.source_id,
            title=args.title,
            url=args.url,
            source_type=args.source_type,
            claims=args.claim,
            tags=args.tags,
            notes=args.notes,
        )

    if args.command == "configure":
        return run_configure(
            config_path=resolved_config_path,
            backend=args.backend,
            profile=args.profile,
            backend_model=args.backend_model,
            model_reasoning_effort=args.model_reasoning_effort,
            project_dir=args.project_dir,
            state_dir=args.state_dir,
            artifacts_dir=args.artifacts_dir,
            codex_command=args.codex_command,
            codex_timeout_seconds=args.codex_timeout_seconds,
            commit_required=args.commit_required,
            progress_update_required=args.progress_update_required,
            repair_on_verification_failure=args.repair_on_verification_failure,
            non_interactive=args.non_interactive,
        )

    if args.command == "simulate-pr":
        return run_simulate_pr(
            repository_language=args.repository_language,
            source_branch=args.source_branch,
            target_branch=args.target_branch,
            commit_sha=args.commit_sha,
        )

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
