"""
GSA Hackathon: Federal AI Litigation-Risk Scoring Tools

Three tools:
  - gsa_gc_score_use_case        -- score one use case by ID
  - gsa_gc_top_risk_use_cases    -- ranked list with optional agency/topic filters
  - gsa_gc_explain_risk_rubric   -- returns the full rubric so the agent can explain it
"""

import json
from pathlib import Path
from typing import Any

from ibm_watsonx_orchestrate.agent_builder.tools import ToolPermission, tool
from pydantic import BaseModel

from tools._data_loader import get_record_by_id, load_records, loaded_at
from tools._scorer import _W, DISCLAIMER, score_all, score_record

# Force Pydantic to resolve all forward references at import time

# ---------------------------------------------------------------------------
# Known-issues loader (Phase 4 stub — reads data/known_issues.json if present)
# ---------------------------------------------------------------------------

_KNOWN_ISSUES_PATH = Path(__file__).resolve().parent.parent / "data" / "known_issues.json"


def _load_known_issues() -> dict[str, list[dict]]:
    """Return dict mapping record_id -> list[evidence_entry], or empty dict."""
    if not _KNOWN_ISSUES_PATH.exists():
        return {}
    try:
        with open(_KNOWN_ISSUES_PATH, encoding="utf-8") as f:
            entries = json.load(f)
        result: dict[str, list[dict]] = {}
        for entry in entries:
            rid = entry.get("use_case_id", "")
            if rid:
                result.setdefault(rid, []).append(entry)
        return result
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ScoreResult(BaseModel):
    record_id: str
    agency: str
    use_case_name: str
    inventory_year: str
    total: int
    band: str
    factors: list[Any]
    missing_fields: list[str]
    disclaimer: str

    model_config = {"arbitrary_types_allowed": True}


class TopRiskResult(BaseModel):
    total_matching: int
    offset: int
    limit: int
    results: list[Any]
    data_loaded_at: str | None = None
    disclaimer: str

    model_config = {"arbitrary_types_allowed": True}


class RubricResult(BaseModel):
    factors: list[Any]
    bands: dict
    disclaimer: str
    config_file: str

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Tool 1: score_use_case
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_score_use_case(use_case_id: str) -> ScoreResult:
    """Score the litigation risk of a single federal AI use case by its record ID.

    Returns an explainable risk score (0-100) with a per-factor breakdown showing
    exactly which fields and values contributed to the score. Includes the risk band
    (Low / Moderate / Elevated), a list of fields that could not be evaluated due to
    missing data, and a disclaimer.

    Args:
        use_case_id (str): The record_id of the use case to score. Obtain IDs from gsa_gc_top_risk_use_cases or gsa_gc_federal_ai_usecase_aggregator.
    Returns:
        ScoreResult: Full risk score including total, band, per-factor breakdown, missing fields, and disclaimer.
    """
    rec = get_record_by_id(use_case_id)
    if rec is None:
        # Try a case-insensitive prefix search as a convenience
        all_recs = load_records()
        matches = [r for r in all_recs if r.record_id.lower().startswith(use_case_id.lower())]
        if not matches:
            return ScoreResult(
                record_id=use_case_id,
                agency="",
                use_case_name="",
                inventory_year="",
                total=0,
                band="Unknown",
                factors=[],
                missing_fields=[f"record_id '{use_case_id}' not found in inventory"],
                disclaimer=DISCLAIMER,
            )
        rec = matches[0]

    ev_map = _load_known_issues()
    result = score_record(rec, ev_map.get(rec.record_id))

    return ScoreResult(
        record_id=rec.record_id,
        agency=rec.agency,
        use_case_name=rec.use_case_name,
        inventory_year=rec.inventory_year,
        total=result["total"],
        band=result["band"],
        factors=result["factors"],
        missing_fields=result["missing_fields"],
        disclaimer=result["disclaimer"],
    )


# ---------------------------------------------------------------------------
# Tool 2: top_risk_use_cases
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_top_risk_use_cases(
    agency: str = "",
    topic: str = "",
    band: str = "",
    limit: int = 20,
    offset: int = 0,
) -> TopRiskResult:
    """Return federal AI use cases ranked by litigation-risk score, with optional filters.

    Scores all matching use cases and returns them sorted highest-risk first.
    Each result includes the record_id, agency, use case name, score, band, and
    the top 3 contributing risk factors.

    Args:
        agency (str): Optional agency name filter (case-insensitive substring match). Leave blank for all agencies.
        topic (str): Optional topic area filter (case-insensitive substring match). Leave blank for all topics.
        band (str): Optional band filter. One of 'Low', 'Moderate', 'Elevated'. Leave blank for all bands.
        limit (int): Maximum number of results to return. Defaults to 20.
        offset (int): Pagination offset. Defaults to 0.
    Returns:
        TopRiskResult: Paginated ranked list of use cases with scores, bands, and top risk factors.
    """
    all_records = load_records()
    ev_map = _load_known_issues()

    # Filter
    filtered = all_records
    if agency:
        filtered = [r for r in filtered if agency.lower() in r.agency.lower()]
    if topic:
        filtered = [r for r in filtered if topic.lower() in (r.topic_area or "").lower()]

    # Score all filtered records
    scored = score_all(filtered, ev_map)

    # Band filter (after scoring)
    if band:
        band_norm = band.strip().capitalize()
        scored = [s for s in scored if s["band"] == band_norm]

    total_matching = len(scored)
    page = scored[offset: offset + limit]

    # Build compact result rows (top 3 factors only to keep payload manageable)
    results = []
    rec_index = {r.record_id: r for r in all_records}
    for s in page:
        rec = rec_index.get(s["record_id"])
        results.append({
            "record_id": s["record_id"],
            "agency": rec.agency if rec else "",
            "bureau": rec.bureau if rec else None,
            "use_case_name": rec.use_case_name if rec else "",
            "inventory_year": rec.inventory_year if rec else "",
            "topic_area": rec.topic_area if rec else None,
            "development_stage": rec.development_stage if rec else None,
            "total": s["total"],
            "band": s["band"],
            "top_factors": s["factors"][:3],
            "missing_fields_count": len(s["missing_fields"]),
        })

    return TopRiskResult(
        total_matching=total_matching,
        offset=offset,
        limit=limit,
        results=results,
        data_loaded_at=loaded_at(),
        disclaimer=DISCLAIMER,
    )


# ---------------------------------------------------------------------------
# Tool 3: explain_risk_rubric
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_explain_risk_rubric() -> RubricResult:
    """Return the full litigation-risk scoring rubric including all factors, weights, and rationale.

    Use this to explain to a user how risk scores are calculated, what each factor
    means, what the band thresholds are, and why certain signals were chosen.

    Args:
    Returns:
        RubricResult: Complete rubric with factors, weights, band definitions, and disclaimer.
    """
    factors = [
        {
            "factor": "High-impact designation",
            "max_points": max(_W["designation"].values()),
            "weights": _W["designation"],
            "rationale": (
                "OMB M-25-21 requires agencies to assess whether their AI use case is 'high-impact' "
                "(significant effect on civil rights, safety, or access to government services). "
                "HIGH_IMPACT = agency confirmed it is. PRESUMED_NOT_HIGH_IMPACT = agency was presumed "
                "high-impact but self-determined it is not — this self-exemption is a notable signal. "
                "UNKNOWN = field not present (all 2024 inventory entries)."
            ),
        },
        {
            "factor": "Affects individuals' rights or benefits",
            "max_points": _W["affects_individuals"]["points"],
            "trigger_topics": _W["affects_individuals"]["trigger_topics"],
            "rationale": (
                "Use cases in law enforcement, benefits, health, housing, employment, or immigration "
                "domains carry inherently higher litigation exposure due to constitutional and statutory "
                "protections (APA, Privacy Act, Equal Credit Opportunity Act, ADA, etc.)."
            ),
        },
        {
            "factor": "Development stage",
            "max_points": _W["development_stage"]["deployed"],
            "weights": _W["development_stage"],
            "rationale": (
                "Deployed systems are in production and actively making or informing decisions. "
                "Pilot systems are in limited operation. Pre-deployment systems pose lower immediate risk."
            ),
        },
        {
            "factor": "Uses PII",
            "max_points": _W["has_pii"]["points"],
            "rationale": (
                "AI use cases involving personally identifiable information create Privacy Act, "
                "FISMA, and potential Fourth Amendment exposure."
            ),
        },
        {
            "factor": "Missing safeguards (HIGH_IMPACT / PRESUMED_NOT_HIGH_IMPACT only)",
            "max_points": _W["missing_safeguards"]["cap"],
            "points_per_missing_safeguard": _W["missing_safeguards"]["points_per_missing"],
            "safeguards_checked": list(_W["missing_safeguards"]["factors"].values()),
            "rationale": (
                "OMB M-25-21 §5 requires high-impact AI to have: an impact assessment, independent review, "
                "ongoing monitoring, and a notice/appeal mechanism. Missing any of these on a self-declared "
                "or presumed-high-impact system is a compliance gap and litigation indicator."
            ),
        },
        {
            "factor": "Waiver from minimum practices",
            "max_points": _W["waiver"]["points"],
            "rationale": (
                "An agency requesting an extension from OMB minimum risk management practices "
                "signals the use case may be operating without required safeguards."
            ),
        },
        {
            "factor": "External evidence (Phase 4)",
            "weights": _W["external_evidence"],
            "rationale": (
                "Known litigation, GAO findings, or IG reports linked to a use case via "
                "data/known_issues.json. These are human-curated — CourtListener matches "
                "are leads, not confirmed matches."
            ),
        },
    ]

    return RubricResult(
        factors=factors,
        bands={
            name: {"min": lo, "max": hi}
            for name, (lo, hi) in _W["bands"].items()
        },
        disclaimer=DISCLAIMER,
        config_file="config/scoring_weights.json",
    )
