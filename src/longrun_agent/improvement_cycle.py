"""Control-plane improvement cycle and research evidence primitives."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from .self_improve import SelfImproveReport


SOURCE_TYPE_CHOICES = ("official", "vendor", "community")


@dataclass(slots=True)
class SourceReference:
    source_id: str
    name: str
    url: str
    source_type: str
    rationale: str
    tags: list[str]


ARCHITECTURE_SOURCES: list[SourceReference] = [
    SourceReference(
        source_id="dora_metrics",
        name="DORA Metrics",
        url="https://dora.dev/guides/dora-metrics/",
        source_type="official",
        rationale="Use operational outcomes as the north-star for process improvement.",
        tags=["metrics", "batch_size", "flow"],
    ),
    SourceReference(
        source_id="sre_error_budget",
        name="Google SRE Error Budget Policy",
        url="https://sre.google/workbook/error-budget-policy/",
        source_type="official",
        rationale="Stop feature acceleration when reliability budgets are exceeded.",
        tags=["reliability", "error_budget"],
    ),
    SourceReference(
        source_id="sre_postmortem",
        name="Google SRE Postmortem Culture",
        url="https://sre.google/sre-book/postmortem-culture/",
        source_type="official",
        rationale="Turn repeated failures into tracked improvement actions.",
        tags=["reliability", "postmortem"],
    ),
    SourceReference(
        source_id="openai_agent_evals",
        name="OpenAI Agent Evals",
        url="https://platform.openai.com/docs/guides/agent-evals",
        source_type="vendor",
        rationale="Use reproducible eval windows to compare strategy changes.",
        tags=["evals", "metrics", "experiments"],
    ),
    SourceReference(
        source_id="anthropic_agent_sdk",
        name="Anthropic Agent SDK Engineering Notes",
        url="https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk",
        source_type="vendor",
        rationale="Agent workflows should include explicit verification before completion.",
        tags=["verification", "reliability"],
    ),
    SourceReference(
        source_id="fowler_feature_toggles",
        name="Feature Toggles (Martin Fowler)",
        url="https://martinfowler.com/articles/feature-toggles.html",
        source_type="community",
        rationale="Ship incrementally to reduce risk and control rollout blast radius.",
        tags=["batch_size", "risk", "rollout", "scoping"],
    ),
]

_SOURCE_BY_ID: dict[str, SourceReference] = {
    item.source_id: item for item in ARCHITECTURE_SOURCES
}


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
    evidence_claim_ids: list[str]
    source_ids: list[str]


@dataclass(slots=True)
class ExperimentPlan:
    experiment_id: str
    hypothesis_id: str
    budget_sessions: int
    actions: list[str]
    success_criteria: list[str]
    rollback_criteria: list[str]
    evidence_claim_ids: list[str]
    source_ids: list[str]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _normalize_tag_text(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_tags(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        candidates = raw.split(",")
    elif isinstance(raw, list):
        candidates = [str(item) for item in raw]
    else:
        candidates = [str(raw)]
    result: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        tag = _normalize_tag_text(item)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    normalized = normalized.strip("_")
    return normalized or "source"


def _derive_source_id(
    *,
    url: str,
    title: str,
    existing_ids: set[str],
) -> str:
    parsed = urlparse(url)
    host = _slugify(parsed.netloc.replace("www.", ""))
    path = _slugify(parsed.path)
    title_part = _slugify(title)
    candidates = [
        f"{host}_{path}" if path else host,
        title_part,
        "research_source",
    ]
    for base in candidates:
        if not base:
            continue
        if base not in existing_ids:
            return base
        index = 2
        while f"{base}_{index}" in existing_ids:
            index += 1
        return f"{base}_{index}"
    return f"research_source_{len(existing_ids) + 1}"


def evidence_file_path(artifacts_dir: Path) -> Path:
    return artifacts_dir / "improvement-evidence.json"


def memory_file_path(artifacts_dir: Path) -> Path:
    return artifacts_dir / "improvement-memory.json"


def _seed_memory_payload() -> dict[str, Any]:
    return {
        "cycles": [],
        "claim_usage": {},
    }


def _coerce_memory_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _seed_memory_payload()

    cycles_raw = payload.get("cycles")
    usage_raw = payload.get("claim_usage")
    cycles: list[dict[str, Any]] = []
    usage: dict[str, dict[str, Any]] = {}

    if isinstance(cycles_raw, list):
        for item in cycles_raw:
            if not isinstance(item, dict):
                continue
            claim_ids = [
                str(claim_id).strip()
                for claim_id in item.get("selected_claim_ids", [])
                if str(claim_id).strip()
            ]
            cycles.append(
                {
                    "generated_at": str(item.get("generated_at", "")).strip() or _now_iso(),
                    "budget_gate_status": str(item.get("budget_gate_status", "")).strip() or "unknown",
                    "primary_bottleneck": str(item.get("primary_bottleneck", "")).strip(),
                    "selected_claim_ids": claim_ids,
                    "hypothesis_ids": [
                        str(hypothesis_id).strip()
                        for hypothesis_id in item.get("hypothesis_ids", [])
                        if str(hypothesis_id).strip()
                    ],
                }
            )

    if isinstance(usage_raw, dict):
        for key, value in usage_raw.items():
            claim_id = str(key).strip()
            if not claim_id:
                continue
            if isinstance(value, dict):
                usage[claim_id] = {
                    "count": int(value.get("count", 0)),
                    "last_used_at": str(value.get("last_used_at", "")).strip() or "",
                }
            else:
                usage[claim_id] = {"count": int(value), "last_used_at": ""}

    return {"cycles": cycles, "claim_usage": usage}


def load_cycle_memory(path: Path) -> dict[str, Any]:
    if not path.exists():
        payload = _seed_memory_payload()
        save_cycle_memory(path, payload)
        return payload
    try:
        raw = json.loads(path.read_text())
    except Exception:
        raw = _seed_memory_payload()
    payload = _coerce_memory_payload(raw)
    save_cycle_memory(path, payload)
    return payload


def save_cycle_memory(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def record_cycle_memory(
    memory: dict[str, Any],
    *,
    generated_at: str,
    budget_gate_status: str,
    primary_bottleneck: str,
    selected_claim_ids: list[str],
    hypothesis_ids: list[str],
) -> dict[str, Any]:
    normalized = _coerce_memory_payload(memory)
    usage = normalized["claim_usage"]
    for claim_id in selected_claim_ids:
        entry = usage.get(claim_id, {"count": 0, "last_used_at": ""})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_used_at"] = generated_at
        usage[claim_id] = entry

    normalized["cycles"].append(
        {
            "generated_at": generated_at,
            "budget_gate_status": budget_gate_status,
            "primary_bottleneck": primary_bottleneck,
            "selected_claim_ids": list(selected_claim_ids),
            "hypothesis_ids": list(hypothesis_ids),
        }
    )
    normalized["cycles"] = normalized["cycles"][-200:]
    return normalized


def _seed_evidence_payload() -> dict[str, Any]:
    sources = [
        {
            "source_id": source.source_id,
            "name": source.name,
            "url": source.url,
            "source_type": source.source_type,
            "rationale": source.rationale,
            "tags": list(source.tags),
            "retrieved_at": _now_iso(),
        }
        for source in ARCHITECTURE_SOURCES
    ]
    claims = [
        {
            "claim_id": "dora_metrics-c1",
            "source_id": "dora_metrics",
            "statement": "Smaller batch sizes and faster feedback loops improve delivery performance.",
            "tags": ["metrics", "batch_size", "flow"],
            "created_at": _now_iso(),
        },
        {
            "claim_id": "sre_error_budget-c1",
            "source_id": "sre_error_budget",
            "statement": "When reliability budgets are exceeded, release velocity should be reduced.",
            "tags": ["reliability", "error_budget", "risk"],
            "created_at": _now_iso(),
        },
        {
            "claim_id": "sre_postmortem-c1",
            "source_id": "sre_postmortem",
            "statement": "Postmortems should produce concrete action items with clear ownership.",
            "tags": ["postmortem", "reliability", "remediation"],
            "created_at": _now_iso(),
        },
        {
            "claim_id": "openai_agent_evals-c1",
            "source_id": "openai_agent_evals",
            "statement": "Agent changes should be evaluated on reproducible task sets before promotion.",
            "tags": ["evals", "metrics", "experiments"],
            "created_at": _now_iso(),
        },
        {
            "claim_id": "anthropic_agent_sdk-c1",
            "source_id": "anthropic_agent_sdk",
            "statement": "Agent workflows should include explicit verification before marking work complete.",
            "tags": ["verification", "reliability"],
            "created_at": _now_iso(),
        },
        {
            "claim_id": "fowler_feature_toggles-c1",
            "source_id": "fowler_feature_toggles",
            "statement": "Incremental rollout and scoped changes reduce production risk.",
            "tags": ["batch_size", "risk", "rollout", "scoping"],
            "created_at": _now_iso(),
        },
    ]
    return {"sources": sources, "claims": claims}


def _coerce_evidence_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"sources": [], "claims": []}

    raw_sources = payload.get("sources")
    raw_claims = payload.get("claims")
    sources: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []

    if isinstance(raw_sources, list):
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id", "")).strip()
            name = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if not source_id or not name or not url:
                continue
            source_type = str(item.get("source_type", "community")).strip().lower()
            if source_type not in SOURCE_TYPE_CHOICES:
                source_type = "community"
            sources.append(
                {
                    "source_id": source_id,
                    "name": name,
                    "url": url,
                    "source_type": source_type,
                    "rationale": str(item.get("rationale", "")).strip(),
                    "tags": _normalize_tags(item.get("tags")),
                    "retrieved_at": str(item.get("retrieved_at", "")).strip() or _now_iso(),
                }
            )

    source_ids = {item["source_id"] for item in sources}
    if isinstance(raw_claims, list):
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            claim_id = str(item.get("claim_id", "")).strip()
            source_id = str(item.get("source_id", "")).strip()
            statement = str(item.get("statement", "")).strip()
            if not source_id or source_id not in source_ids or not statement:
                continue
            if not claim_id:
                claim_id = f"{source_id}-c{len(claims) + 1}"
            claims.append(
                {
                    "claim_id": claim_id,
                    "source_id": source_id,
                    "statement": statement,
                    "tags": _normalize_tags(item.get("tags")),
                    "created_at": str(item.get("created_at", "")).strip() or _now_iso(),
                }
            )

    return {"sources": sources, "claims": claims}


def load_research_evidence(path: Path, *, bootstrap_if_missing: bool = True) -> dict[str, Any]:
    if not path.exists():
        payload = _seed_evidence_payload() if bootstrap_if_missing else {"sources": [], "claims": []}
        save_research_evidence(path, payload)
        return payload
    try:
        raw = json.loads(path.read_text())
    except Exception:
        raw = {"sources": [], "claims": []}
    payload = _coerce_evidence_payload(raw)
    save_research_evidence(path, payload)
    return payload


def save_research_evidence(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def add_research_evidence(
    *,
    path: Path,
    source_id: str,
    title: str,
    url: str,
    source_type: str,
    claims: list[str],
    tags: list[str],
    notes: str = "",
) -> dict[str, Any]:
    payload = load_research_evidence(path, bootstrap_if_missing=True)
    source_type_normalized = source_type.strip().lower()
    if source_type_normalized not in SOURCE_TYPE_CHOICES:
        raise ValueError(f"source_type must be one of {', '.join(SOURCE_TYPE_CHOICES)}")

    source_id = source_id.strip()
    title = title.strip()
    url = url.strip()
    if not source_id or not title or not url:
        raise ValueError("source_id/title/url are required")

    tags_normalized = _normalize_tags(tags)
    sources = [item for item in payload["sources"] if item.get("source_id") != source_id]
    sources.append(
        {
            "source_id": source_id,
            "name": title,
            "url": url,
            "source_type": source_type_normalized,
            "rationale": notes.strip(),
            "tags": tags_normalized,
            "retrieved_at": _now_iso(),
        }
    )
    payload["sources"] = sorted(sources, key=lambda item: str(item.get("source_id", "")))

    existing_claims = [item for item in payload["claims"] if item.get("source_id") == source_id]
    next_index = len(existing_claims) + 1
    for raw_claim in claims:
        statement = raw_claim.strip()
        if not statement:
            continue
        payload["claims"].append(
            {
                "claim_id": f"{source_id}-c{next_index}",
                "source_id": source_id,
                "statement": statement,
                "tags": tags_normalized,
                "created_at": _now_iso(),
            }
        )
        next_index += 1

    payload = _coerce_evidence_payload(payload)
    save_research_evidence(path, payload)
    return payload


def add_research_bundle(
    *,
    path: Path,
    sources: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    default_source_type: str = "community",
) -> dict[str, Any]:
    payload = load_research_evidence(path, bootstrap_if_missing=True)
    payload = _coerce_evidence_payload(payload)

    source_type_fallback = default_source_type.strip().lower()
    if source_type_fallback not in SOURCE_TYPE_CHOICES:
        source_type_fallback = "community"

    source_map_by_id: dict[str, dict[str, Any]] = {
        item["source_id"]: item for item in payload["sources"]
    }
    source_id_by_url: dict[str, str] = {
        str(item.get("url", "")).strip(): item["source_id"]
        for item in payload["sources"]
        if str(item.get("url", "")).strip()
    }
    existing_ids = set(source_map_by_id.keys())

    for source in sources:
        url = str(source.get("url", "")).strip()
        title = str(source.get("name", source.get("title", ""))).strip()
        if not url or not title:
            continue
        source_id = str(source.get("source_id", "")).strip()
        if not source_id:
            source_id = source_id_by_url.get(url, "")
        if not source_id:
            source_id = _derive_source_id(url=url, title=title, existing_ids=existing_ids)
        source_type = str(source.get("source_type", source_type_fallback)).strip().lower()
        if source_type not in SOURCE_TYPE_CHOICES:
            source_type = source_type_fallback
        tags = _normalize_tags(source.get("tags"))
        existing = source_map_by_id.get(source_id, {})
        merged_tags = _normalize_tags(list(existing.get("tags", [])) + tags)
        merged = {
            "source_id": source_id,
            "name": title,
            "url": url,
            "source_type": source_type,
            "rationale": str(source.get("rationale", existing.get("rationale", ""))).strip(),
            "tags": merged_tags,
            "retrieved_at": _now_iso(),
        }
        source_map_by_id[source_id] = merged
        source_id_by_url[url] = source_id
        existing_ids.add(source_id)

    payload["sources"] = sorted(source_map_by_id.values(), key=lambda item: item["source_id"])

    existing_claim_pairs = {
        (
            str(item.get("source_id", "")).strip(),
            str(item.get("statement", "")).strip().lower(),
        )
        for item in payload["claims"]
        if str(item.get("source_id", "")).strip() and str(item.get("statement", "")).strip()
    }
    max_index_by_source: dict[str, int] = {}
    for claim in payload["claims"]:
        source_id = str(claim.get("source_id", "")).strip()
        claim_id = str(claim.get("claim_id", "")).strip()
        if not source_id or not claim_id.startswith(f"{source_id}-c"):
            continue
        try:
            index = int(claim_id.split("-c")[-1])
        except ValueError:
            continue
        max_index_by_source[source_id] = max(max_index_by_source.get(source_id, 0), index)

    for claim in claims:
        statement = str(claim.get("statement", "")).strip()
        if not statement:
            continue
        source_id = str(claim.get("source_id", "")).strip()
        if not source_id:
            source_url = str(claim.get("source_url", "")).strip()
            source_id = source_id_by_url.get(source_url, "")
        if not source_id or source_id not in source_map_by_id:
            continue

        key = (source_id, statement.lower())
        if key in existing_claim_pairs:
            continue
        existing_claim_pairs.add(key)

        max_index_by_source[source_id] = max_index_by_source.get(source_id, 0) + 1
        claim_id = str(claim.get("claim_id", "")).strip()
        if not claim_id:
            claim_id = f"{source_id}-c{max_index_by_source[source_id]}"
        payload["claims"].append(
            {
                "claim_id": claim_id,
                "source_id": source_id,
                "statement": statement,
                "tags": _normalize_tags(
                    list(source_map_by_id[source_id].get("tags", []))
                    + _normalize_tags(claim.get("tags"))
                ),
                "created_at": _now_iso(),
            }
        )

    payload = _coerce_evidence_payload(payload)
    save_research_evidence(path, payload)
    return payload


def _source_lookup(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["source_id"]: item for item in evidence.get("sources", [])}


def _memory_usage_count(memory: dict[str, Any], claim_id: str) -> int:
    usage = memory.get("claim_usage", {})
    if not isinstance(usage, dict):
        return 0
    raw_entry = usage.get(claim_id, {})
    if isinstance(raw_entry, dict):
        try:
            return max(0, int(raw_entry.get("count", 0)))
        except Exception:
            return 0
    try:
        return max(0, int(raw_entry))
    except Exception:
        return 0


def _last_cycle_claim_ids(memory: dict[str, Any]) -> set[str]:
    cycles = memory.get("cycles", [])
    if not isinstance(cycles, list) or not cycles:
        return set()
    last = cycles[-1]
    if not isinstance(last, dict):
        return set()
    return {
        str(item).strip()
        for item in last.get("selected_claim_ids", [])
        if str(item).strip()
    }


def select_research_claims_for_diagnosis(
    evidence: dict[str, Any],
    diagnosis: Diagnosis,
    *,
    memory: dict[str, Any] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    claims = [item for item in evidence.get("claims", []) if isinstance(item, dict)]
    if not claims:
        return []
    memory_payload = _coerce_memory_payload(memory if memory is not None else _seed_memory_payload())
    recent_claim_ids = _last_cycle_claim_ids(memory_payload)

    required_tags: set[str] = {"metrics"}
    if diagnosis.primary_bottleneck.startswith("gate:"):
        required_tags.update({"reliability", "verification", "postmortem"})
    if diagnosis.failure_rate > 0.10:
        required_tags.update({"reliability", "error_budget", "risk"})
    if diagnosis.no_progress_rate > 0.20:
        required_tags.update({"batch_size", "scoping", "flow"})

    sources = _source_lookup(evidence)
    scored: list[tuple[float, dict[str, Any]]] = []
    for claim in claims:
        claim_id = str(claim.get("claim_id", "")).strip()
        if not claim_id:
            continue
        tags = set(_normalize_tags(claim.get("tags")))
        overlap = len(tags.intersection(required_tags))
        source = sources.get(str(claim.get("source_id", "")), {})
        source_type = str(source.get("source_type", "community"))
        source_bonus = 0.20 if source_type == "official" else 0.10 if source_type == "vendor" else 0.05
        base_score = float(overlap) + source_bonus
        if overlap == 0 and required_tags:
            continue
        usage_count = _memory_usage_count(memory_payload, claim_id)
        recency_penalty = 0.60 if claim_id in recent_claim_ids else 0.0
        score = base_score - (0.30 * float(usage_count)) - recency_penalty
        enriched = dict(claim)
        enriched["tags"] = sorted(tags)
        enriched["source"] = source
        enriched["usage_count"] = usage_count
        enriched["used_in_last_cycle"] = claim_id in recent_claim_ids
        enriched["score"] = score
        scored.append((score, enriched))

    if not scored:
        fallback: list[dict[str, Any]] = []
        for claim in claims[: min(3, len(claims))]:
            source = sources.get(str(claim.get("source_id", "")), {})
            item = dict(claim)
            item["source"] = source
            item["tags"] = _normalize_tags(item.get("tags"))
            claim_id = str(item.get("claim_id", "")).strip()
            item["usage_count"] = _memory_usage_count(memory_payload, claim_id)
            item["used_in_last_cycle"] = claim_id in recent_claim_ids
            item["score"] = 0.0
            fallback.append(item)
        return fallback

    scored.sort(
        key=lambda item: (
            int(item[1].get("usage_count", 0)),
            bool(item[1].get("used_in_last_cycle", False)),
            -item[0],
            str(item[1].get("claim_id", "")),
        )
    )
    selected = [item for _, item in scored[:limit]]

    # Extra anti-repeat guard: avoid selecting exactly the same claim set as last cycle
    # when alternative candidates exist.
    selected_ids = {
        str(item.get("claim_id", "")).strip() for item in selected if str(item.get("claim_id", "")).strip()
    }
    if selected_ids and selected_ids == recent_claim_ids:
        alternatives = [
            item
            for _, item in scored[limit:]
            if str(item.get("claim_id", "")).strip() not in recent_claim_ids
        ]
        if alternatives and selected:
            selected[-1] = alternatives[0]

    return selected


def _source_ids_from_claim_ids(
    claim_ids: list[str],
    claim_lookup: dict[str, dict[str, Any]],
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for claim_id in claim_ids:
        claim = claim_lookup.get(claim_id)
        if claim is None:
            continue
        source_id = str(claim.get("source_id", "")).strip()
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        result.append(source_id)
    return result


def _claim_ids_for_tags(
    claims: list[dict[str, Any]],
    wanted_tags: set[str],
    *,
    fallback_to_all: bool = True,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        claim_id = str(claim.get("claim_id", "")).strip()
        if not claim_id or claim_id in seen:
            continue
        tags = set(_normalize_tags(claim.get("tags")))
        if tags.intersection(wanted_tags):
            seen.add(claim_id)
            result.append(claim_id)
    if result or not fallback_to_all:
        return result
    for claim in claims:
        claim_id = str(claim.get("claim_id", "")).strip()
        if not claim_id or claim_id in seen:
            continue
        seen.add(claim_id)
        result.append(claim_id)
    return result


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


def enforce_research_requirement(
    budget_gate: BudgetGateDecision,
    selected_claims: list[dict[str, Any]],
) -> BudgetGateDecision:
    reasons = list(budget_gate.reasons)
    if not selected_claims:
        reasons.append("insufficient_research_evidence: add evidence claims before planning")
    return BudgetGateDecision(
        status="promote" if not reasons else "hold",
        failure_rate=budget_gate.failure_rate,
        no_progress_rate=budget_gate.no_progress_rate,
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


def build_hypotheses(
    diagnosis: Diagnosis,
    selected_claims: list[dict[str, Any]],
) -> list[Hypothesis]:
    claim_lookup = {
        str(item.get("claim_id", "")): item for item in selected_claims if str(item.get("claim_id", "")).strip()
    }
    hypotheses: list[Hypothesis] = []

    if not selected_claims:
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h-research-required",
                statement=(
                    "No relevant research claims were selected; collect evidence first, then design hypotheses."
                ),
                expected_outcome="Evidence-backed hypothesis generation becomes possible.",
                evidence_claim_ids=[],
                source_ids=[],
            )
        )
        return hypotheses

    if diagnosis.top_gate_failures:
        gate_id, count = diagnosis.top_gate_failures[0]
        claim_ids = _claim_ids_for_tags(
            selected_claims,
            {"reliability", "verification", "postmortem", "error_budget"},
        )
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h1-top-gate-mitigation",
                statement=(
                    f"Reducing failures on {gate_id} will lower end-to-end session failure rate; "
                    f"current count={count}."
                ),
                expected_outcome="Lower failure_rate and fewer remediation reports in next window.",
                evidence_claim_ids=claim_ids,
                source_ids=_source_ids_from_claim_ids(claim_ids, claim_lookup),
            )
        )

    if diagnosis.no_progress_rate > 0.20:
        claim_ids = _claim_ids_for_tags(
            selected_claims,
            {"batch_size", "scoping", "flow", "metrics"},
        )
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h2-no-progress-reduction",
                statement=(
                    "Reducing per-session scope and tightening acceptance criteria "
                    "will reduce no-progress coding sessions."
                ),
                expected_outcome="no_progress_rate drops below threshold window-over-window.",
                evidence_claim_ids=claim_ids,
                source_ids=_source_ids_from_claim_ids(claim_ids, claim_lookup),
            )
        )

    if diagnosis.failure_rate > 0.10:
        claim_ids = _claim_ids_for_tags(
            selected_claims,
            {"error_budget", "reliability", "metrics", "risk"},
        )
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h3-reliability-budget-first",
                statement=(
                    "Enforcing error-budget style gate decisions will avoid compounding failures "
                    "and improve stability before adding feature scope."
                ),
                expected_outcome="failure_rate trends down and hold decisions become less frequent.",
                evidence_claim_ids=claim_ids,
                source_ids=_source_ids_from_claim_ids(claim_ids, claim_lookup),
            )
        )

    if not hypotheses:
        claim_ids = _claim_ids_for_tags(selected_claims, {"metrics"}, fallback_to_all=True)
        hypotheses.append(
            Hypothesis(
                hypothesis_id="h0-continue",
                statement="Current process is stable enough to continue with the same strategy.",
                expected_outcome="Metrics remain within targets over the next window.",
                evidence_claim_ids=claim_ids,
                source_ids=_source_ids_from_claim_ids(claim_ids, claim_lookup),
            )
        )

    return hypotheses


def build_experiment_plans(
    hypotheses: list[Hypothesis],
    targets: ImprovementTargets,
    selected_claims: list[dict[str, Any]],
) -> list[ExperimentPlan]:
    claim_lookup = {
        str(item.get("claim_id", "")): item for item in selected_claims if str(item.get("claim_id", "")).strip()
    }
    plans: list[ExperimentPlan] = []
    for index, hypothesis in enumerate(hypotheses, start=1):
        evidence_claim_ids = list(hypothesis.evidence_claim_ids)
        if not evidence_claim_ids:
            evidence_claim_ids = _claim_ids_for_tags(
                selected_claims,
                {"metrics"},
                fallback_to_all=True,
            )[:3]
        source_ids = _source_ids_from_claim_ids(evidence_claim_ids, claim_lookup)
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
                evidence_claim_ids=evidence_claim_ids,
                source_ids=source_ids,
            )
        )
    return plans


def _render_source_links(source_ids: list[str], sources_by_id: dict[str, dict[str, Any]]) -> str:
    links: list[str] = []
    for source_id in source_ids:
        source = sources_by_id.get(source_id)
        if source is None:
            continue
        links.append(f"[{source['name']}]({source['url']})")
    return ", ".join(links) if links else "none"


def build_cycle_payload(
    *,
    report: SelfImproveReport,
    targets: ImprovementTargets,
    diagnosis: Diagnosis,
    budget_gate: BudgetGateDecision,
    hypotheses: list[Hypothesis],
    experiment_plans: list[ExperimentPlan],
    research_evidence: dict[str, Any],
    selected_research_claims: list[dict[str, Any]],
    cycle_memory: dict[str, Any],
) -> dict[str, Any]:
    recent_cycles = cycle_memory.get("cycles", [])
    if isinstance(recent_cycles, list):
        recent_cycles = recent_cycles[-5:]
    else:
        recent_cycles = []
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
        "selected_research_claims": selected_research_claims,
        "research_sources": research_evidence.get("sources", []),
        "selection_policy": {
            "prefer_low_usage_claims": True,
            "avoid_last_cycle_claim_set": True,
        },
        "memory": {
            "recent_cycles": recent_cycles,
            "claim_usage": cycle_memory.get("claim_usage", {}),
        },
    }


def render_cycle_markdown(payload: dict[str, Any]) -> str:
    diagnosis = payload["diagnosis"]
    budget_gate = payload["budget_gate"]
    hypotheses = payload["hypotheses"]
    plans = payload["experiment_plans"]
    selected_claims = payload.get("selected_research_claims", [])
    sources = payload.get("research_sources", [])
    memory = payload.get("memory", {})
    sources_by_id = {item["source_id"]: item for item in sources if isinstance(item, dict)}

    top_gates = diagnosis.get("top_gate_failures", [])
    if top_gates:
        gate_lines = "\n".join(f"- `{gate}`: {count}" for gate, count in top_gates)
    else:
        gate_lines = "- none"

    if selected_claims:
        evidence_lines = "\n".join(
            [
                f"- `{item['claim_id']}` ({item['source_id']}): {item['statement']} "
                f"(usage_count={item.get('usage_count', 0)}, "
                f"used_last_cycle={item.get('used_in_last_cycle', False)})"
                for item in selected_claims
            ]
        )
    else:
        evidence_lines = "- none"

    hypothesis_lines = "\n".join(
        [
            f"{index}. `{item['hypothesis_id']}` - {item['statement']}\n"
            f"   expected: {item['expected_outcome']}\n"
            f"   evidence_claims: {', '.join(item.get('evidence_claim_ids', [])) or 'none'}\n"
            f"   sources: {_render_source_links(item.get('source_ids', []), sources_by_id)}"
            for index, item in enumerate(hypotheses, start=1)
        ]
    )
    plan_lines = "\n".join(
        [
            f"{index}. `{item['experiment_id']}` ({item['hypothesis_id']})\n"
            f"   budget_sessions: {item['budget_sessions']}\n"
            f"   success: {', '.join(item['success_criteria'])}\n"
            f"   evidence_claims: {', '.join(item.get('evidence_claim_ids', [])) or 'none'}\n"
            f"   sources: {_render_source_links(item.get('source_ids', []), sources_by_id)}"
            for index, item in enumerate(plans, start=1)
        ]
    )
    source_lines = "\n".join(
        [
            f"- `{item['source_id']}` [{item['name']}]({item['url']}) "
            f"(type={item.get('source_type', 'community')}): {item.get('rationale', '')}"
            for item in sources
        ]
    )
    recent_cycles = memory.get("recent_cycles", [])
    if isinstance(recent_cycles, list) and recent_cycles:
        history_lines = "\n".join(
            [
                f"- {item.get('generated_at', '')} status={item.get('budget_gate_status', '')} "
                f"claims={', '.join(item.get('selected_claim_ids', [])) or 'none'}"
                for item in recent_cycles
                if isinstance(item, dict)
            ]
        )
    else:
        history_lines = "- none"

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
        "## Research Evidence\n"
        f"{evidence_lines}\n\n"
        "## Hypotheses\n"
        f"{hypothesis_lines}\n\n"
        "## Experiment Plans\n"
        f"{plan_lines}\n\n"
        "## Research Sources\n"
        f"{source_lines}\n\n"
        "## Memory History\n"
        f"{history_lines}\n"
    )
