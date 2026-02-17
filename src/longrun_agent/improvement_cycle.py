"""Control-plane architecture for improving longrun-agent with longrun-agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from .self_improve import SelfImproveReport


@dataclass(slots=True)
class SourceReference:
    name: str
    url: str
    rationale: str


ARCHITECTURE_SOURCES: list[SourceReference] = [
    SourceReference(
        name="DORA Metrics",
        url="https://dora.dev/guides/dora-metrics/",
        rationale="Use operational outcomes as the north-star for process improvement.",
    ),
    SourceReference(
        name="Google SRE Error Budget Policy",
        url="https://sre.google/workbook/error-budget-policy/",
        rationale="Stop feature acceleration when reliability budgets are exceeded.",
    ),
    SourceReference(
        name="Google SRE Postmortem Culture",
        url="https://sre.google/sre-book/postmortem-culture/",
        rationale="Turn repeated failures into tracked improvement actions.",
    ),
    SourceReference(
        name="OpenAI Agent Evals",
        url="https://platform.openai.com/docs/guides/agent-evals",
        rationale="Use reproducible eval windows to compare strategy changes.",
    ),
    SourceReference(
        name="OpenTelemetry Logs",
        url="https://opentelemetry.io/docs/specs/otel/logs/",
        rationale="Keep execution telemetry structured and correlated across sessions.",
    ),
]


@dataclass(slots=True)
class ImprovementTargets:
    max_failure_rate: float = 0.10
    max_no_progress_rate: float = 0.25
    min_sessions: int = 10


@dataclass(slots=True)
class BudgetGateDecision:
    status: str
    failure_rate: float
    no_progress_rate: float
    reasons: list[str]


@dataclass(slots=True)
class Diagnosis:
    session_count: int
    failure_count: int
    coding_sessions: int
    no_progress_sessions: int
    failure_rate: float
    no_progress_rate: float
    top_gate_failures: list[tuple[str, int]]
    primary_bottleneck: str


@dataclass(slots=True)
class Hypothesis:
    hypothesis_id: str
    statement: str
    expected_outcome: str


@dataclass(slots=True)
class ExperimentPlan:
    experiment_id: str
    hypothesis_id: str
    budget_sessions: int
    actions: list[str]
    success_criteria: list[str]
    rollback_criteria: list[str]


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def evaluate_budget_gate(
    *,
    session_count: int,
    failure_count: int,
    coding_sessions: int,
    no_progress_sessions: int,
    targets: ImprovementTargets,
) -> BudgetGateDecision:
    failure_rate = _safe_rate(failure_count, session_count)
    no_progress_rate = _safe_rate(no_progress_sessions, coding_sessions)

    reasons: list[str] = []
    if session_count < targets.min_sessions:
        reasons.append(
            "insufficient_data: "
            f"sessions={session_count} < min_sessions={targets.min_sessions}"
        )
    if failure_rate > targets.max_failure_rate:
        reasons.append(
            "failure_rate_exceeded: "
            f"failure_rate={failure_rate:.3f} > target={targets.max_failure_rate:.3f}"
        )
    if no_progress_rate > targets.max_no_progress_rate:
        reasons.append(
            "no_progress_rate_exceeded: "
            f"no_progress_rate={no_progress_rate:.3f} > target={targets.max_no_progress_rate:.3f}"
        )

    return BudgetGateDecision(
        status="promote" if not reasons else "hold",
        failure_rate=failure_rate,
        no_progress_rate=no_progress_rate,
        reasons=reasons,
    )


def build_diagnosis(report: SelfImproveReport) -> Diagnosis:
    failure_rate = _safe_rate(report.failure_count, report.session_count)
    no_progress_rate = _safe_rate(report.no_progress_sessions, report.coding_sessions)
    top_gate_failures = sorted(
        report.gate_failures.items(),
        key=lambda item: (-item[1], item[0]),
    )[:3]

    if top_gate_failures:
        primary_bottleneck = f"gate:{top_gate_failures[0][0]}"
    elif failure_rate > 0.0:
        primary_bottleneck = "session_failure_rate"
    elif no_progress_rate > 0.0:
        primary_bottleneck = "coding_no_progress_rate"
    else:
        primary_bottleneck = "none_detected"

    return Diagnosis(
        session_count=report.session_count,
        failure_count=report.failure_count,
        coding_sessions=report.coding_sessions,
        no_progress_sessions=report.no_progress_sessions,
        failure_rate=failure_rate,
        no_progress_rate=no_progress_rate,
        top_gate_failures=top_gate_failures,
        primary_bottleneck=primary_bottleneck,
    )


def build_hypotheses(diagnosis: Diagnosis) -> list[Hypothesis]:
    hypotheses: list[Hypothesis] = []

    if diagnosis.top_gate_failures:
        gate_id, count = diagnosis.top_gate_failures[0]
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h1-top-gate-mitigation",
                statement=(
                    f"Reducing failures on {gate_id} will lower end-to-end session failure rate; "
                    f"current count={count}."
                ),
                expected_outcome="Lower failure_rate and fewer remediation reports in next window.",
            )
        )

    if diagnosis.no_progress_rate > 0.20:
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h2-no-progress-reduction",
                statement=(
                    "Reducing per-session scope and tightening acceptance criteria "
                    "will reduce no-progress coding sessions."
                ),
                expected_outcome="no_progress_rate drops below threshold window-over-window.",
            )
        )

    if diagnosis.failure_rate > 0.10:
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h3-reliability-budget-first",
                statement=(
                    "Enforcing error-budget style gate decisions will avoid compounding failures "
                    "and improve stability before adding feature scope."
                ),
                expected_outcome="failure_rate trends down and hold decisions become less frequent.",
            )
        )

    if not hypotheses:
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h0-continue",
                statement="Current process is stable enough to continue with the same strategy.",
                expected_outcome="Metrics remain within targets over the next window.",
            )
        )

    return hypotheses


def build_experiment_plans(
    hypotheses: list[Hypothesis],
    targets: ImprovementTargets,
) -> list[ExperimentPlan]:
    plans: list[ExperimentPlan] = []
    for index, hypothesis in enumerate(hypotheses, start=1):
        plans.append(
            ExperimentPlan(
                experiment_id=f"exp-{index:02d}",
                hypothesis_id=hypothesis.hypothesis_id,
                budget_sessions=8,
                actions=[
                    "Create a focused feature batch for this hypothesis only.",
                    "Run longrun-agent run-loop with max 8 sessions for the batch.",
                    "Collect session/remediation evidence and compare to previous window.",
                ],
                success_criteria=[
                    f"failure_rate <= {targets.max_failure_rate:.2f}",
                    f"no_progress_rate <= {targets.max_no_progress_rate:.2f}",
                ],
                rollback_criteria=[
                    "Two consecutive windows with degraded failure_rate.",
                    "Primary bottleneck gate count increases after rollout.",
                ],
            )
        )
    return plans


def build_cycle_payload(
    *,
    report: SelfImproveReport,
    targets: ImprovementTargets,
    diagnosis: Diagnosis,
    budget_gate: BudgetGateDecision,
    hypotheses: list[Hypothesis],
    experiment_plans: list[ExperimentPlan],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "targets": asdict(targets),
        "diagnosis": asdict(diagnosis),
        "raw_window": {
            "window": report.window,
            "session_count": report.session_count,
            "failure_count": report.failure_count,
            "coding_sessions": report.coding_sessions,
            "no_progress_sessions": report.no_progress_sessions,
            "gate_failures": report.gate_failures,
        },
        "budget_gate": asdict(budget_gate),
        "hypotheses": [asdict(item) for item in hypotheses],
        "experiment_plans": [asdict(item) for item in experiment_plans],
        "architecture_sources": [asdict(item) for item in ARCHITECTURE_SOURCES],
    }


def render_cycle_markdown(payload: dict[str, Any]) -> str:
    diagnosis = payload["diagnosis"]
    budget_gate = payload["budget_gate"]
    hypotheses = payload["hypotheses"]
    plans = payload["experiment_plans"]
    sources = payload["architecture_sources"]

    top_gates = diagnosis.get("top_gate_failures", [])
    if top_gates:
        gate_lines = "\n".join(f"- `{gate}`: {count}" for gate, count in top_gates)
    else:
        gate_lines = "- none"

    hypothesis_lines = "\n".join(
        [
            f"{index}. `{item['hypothesis_id']}` - {item['statement']}\n"
            f"   expected: {item['expected_outcome']}"
            for index, item in enumerate(hypotheses, start=1)
        ]
    )
    plan_lines = "\n".join(
        [
            f"{index}. `{item['experiment_id']}` ({item['hypothesis_id']})\n"
            f"   budget_sessions: {item['budget_sessions']}\n"
            f"   success: {', '.join(item['success_criteria'])}"
            for index, item in enumerate(plans, start=1)
        ]
    )
    source_lines = "\n".join(
        [f"- [{item['name']}]({item['url']}): {item['rationale']}" for item in sources]
    )

    return (
        "# Improvement Cycle Report\n\n"
        f"Generated at: {payload['generated_at']}\n\n"
        "## Diagnose\n"
        f"- sessions: {diagnosis['session_count']}\n"
        f"- failures: {diagnosis['failure_count']}\n"
        f"- failure_rate: {diagnosis['failure_rate']:.3f}\n"
        f"- no_progress_rate: {diagnosis['no_progress_rate']:.3f}\n"
        f"- primary_bottleneck: {diagnosis['primary_bottleneck']}\n"
        "- top gate failures:\n"
        f"{gate_lines}\n\n"
        "## Budget Gate\n"
        f"- status: {budget_gate['status']}\n"
        f"- reasons: {', '.join(budget_gate['reasons']) if budget_gate['reasons'] else 'none'}\n\n"
        "## Hypotheses\n"
        f"{hypothesis_lines}\n\n"
        "## Experiment Plans\n"
        f"{plan_lines}\n\n"
        "## Architecture Sources\n"
        f"{source_lines}\n"
    )
