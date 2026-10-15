"""
Deterministic litigation-risk scoring engine.

Pure Python — no LLM calls, no network calls. Reads weights from
config/scoring_weights.json. Import and call score_record() directly.

Score structure
---------------
{
  "record_id": str,
  "total": int,           # 0-100, capped
  "band": str,            # "Low" | "Moderate" | "Elevated"
  "factors": [            # ordered by points desc
    {
      "factor": str,
      "points": int,
      "source_field": str,
      "source_value": str | None,
      "rationale": str,
    }
  ],
  "missing_fields": [str],  # fields that were None/unknown, reducing certainty
  "disclaimer": str,
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools._data_loader import (
    HI_HIGH_IMPACT,
    HI_NOT_HIGH_IMPACT,
    HI_PRESUMED_NOT_HIGH_IMPACT,
    HI_UNKNOWN,
    STAGE_DEPLOYED,
    STAGE_PILOT,
    NormalizedRecord,
)

# ---------------------------------------------------------------------------
# Load weights once at module import
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring_weights.json"


def _strip_comments(obj: Any) -> Any:
    """Recursively remove keys starting with '_' from dicts."""
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(i) for i in obj]
    return obj


def _load_weights() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return _strip_comments(raw)


_W: dict[str, Any] = _load_weights()
DISCLAIMER: str = _W["disclaimer"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _band(total: int) -> str:
    for band_name, bounds in _W["bands"].items():
        if not isinstance(bounds, list):
            continue
        lo, hi = int(bounds[0]), int(bounds[1])
        if lo <= total <= hi:
            return band_name
    return "Elevated"


def _safeguard_missing(value: str | None) -> bool:
    """Return True when a safeguard field clearly indicates it is NOT in place."""
    if value is None:
        return False   # unknown — don't penalise for missing data
    v = value.strip().lower()
    # Positive signals — safeguard present
    if v.startswith("yes") or v in ("appeal process established", "monitoring established",
                                    "oversight board review", "caio review", "in-progress"):
        return False
    # Waived or explicitly absent
    if v in ("no", "n", "caio waived") or v.startswith("no "):
        return True
    return False


def _is_yes(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("yes", "y")


# ---------------------------------------------------------------------------
# Topic / rights-impact triggers
# ---------------------------------------------------------------------------

_TRIGGER_TOPICS: set[str] = {t.lower() for t in _W["affects_individuals"]["trigger_topics"]}
_RIGHTS_TRIGGERS: set[str] = {t.lower() for t in _W["affects_individuals"]["rights_safety_triggers"]}


def _affects_individuals(rec: NormalizedRecord) -> bool:
    topic = (rec.topic_area or "").lower()
    if topic in _TRIGGER_TOPICS:
        return True
    for field_val in [rec.is_rights_impacting, rec.is_safety_impacting]:
        if field_val and field_val.lower() in _RIGHTS_TRIGGERS:
            return True
    return False


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def score_record(
    rec: NormalizedRecord,
    external_evidence: list[dict] | None = None,
) -> dict:
    """Score a single NormalizedRecord.

    Args:
        rec: The normalised use-case record to score.
        external_evidence: Optional list of known-issues entries (Phase 4).
            Each entry should have a ``type`` field: ``litigation`` | ``gao`` | ``ig`` | ``other``.

    Returns:
        dict: Full score result including total, band, factor breakdown,
              missing_fields list, and disclaimer.
    """
    factors: list[dict] = []
    missing_fields: list[str] = []

    # ------------------------------------------------------------------
    # 1. High-impact designation
    # ------------------------------------------------------------------
    designation_pts = _W["designation"].get(rec.hi_designation, 0)
    factors.append({
        "factor": "High-impact designation",
        "points": designation_pts,
        "source_field": "hi_designation",
        "source_value": rec.hi_designation,
        "rationale": (
            "Agency self-reported as HIGH_IMPACT under OMB M-25-21."
            if rec.hi_designation == HI_HIGH_IMPACT
            else "Agency determined NOT high-impact after starting from the presumption it was."
            if rec.hi_designation == HI_PRESUMED_NOT_HIGH_IMPACT
            else "Agency self-reported as NOT high-impact."
            if rec.hi_designation == HI_NOT_HIGH_IMPACT
            else "Designation not reported in this inventory year (2024 data has no designation field)."
        ),
    })
    if rec.hi_designation == HI_UNKNOWN:
        missing_fields.append("hi_designation")

    # ------------------------------------------------------------------
    # 2. Affects individuals' rights or benefits
    # ------------------------------------------------------------------
    if _affects_individuals(rec):
        pts = _W["affects_individuals"]["points"]
        triggered_by = (
            f"topic_area={rec.topic_area!r}"
            if (rec.topic_area or "").lower() in _TRIGGER_TOPICS
            else f"is_rights_impacting={rec.is_rights_impacting!r} or is_safety_impacting={rec.is_safety_impacting!r}"
        )
        factors.append({
            "factor": "Affects individuals' rights or benefits",
            "points": pts,
            "source_field": "topic_area / is_rights_impacting / is_safety_impacting",
            "source_value": triggered_by,
            "rationale": "Topic area or rights/safety field indicates direct impact on individuals.",
        })
    else:
        missing_fields.append("affects_individuals_signal")

    # ------------------------------------------------------------------
    # 3. Development stage
    # ------------------------------------------------------------------
    stage_pts = _W["development_stage"].get(rec.development_stage, 0)
    if stage_pts > 0:
        factors.append({
            "factor": "Development stage",
            "points": stage_pts,
            "source_field": "development_stage",
            "source_value": rec.development_stage,
            "rationale": f"Use case is {rec.development_stage} — live systems carry greater risk exposure.",
        })
    if rec.development_stage in (STAGE_DEPLOYED, STAGE_PILOT) and stage_pts == 0 or rec.development_stage == "unknown":
        missing_fields.append("development_stage")

    # ------------------------------------------------------------------
    # 4. Uses PII
    # ------------------------------------------------------------------
    if _is_yes(rec.has_pii):
        factors.append({
            "factor": "Uses PII",
            "points": _W["has_pii"]["points"],
            "source_field": "has_pii",
            "source_value": rec.has_pii,
            "rationale": "Use case involves personally identifiable information.",
        })
    elif rec.has_pii is None:
        missing_fields.append("has_pii")

    # ------------------------------------------------------------------
    # 5. Missing safeguards (only for HIGH_IMPACT / PRESUMED_NOT_HIGH_IMPACT)
    # ------------------------------------------------------------------
    safeguard_pts = 0
    safeguard_details: list[dict] = []
    if rec.hi_designation in _W["missing_safeguards"]["applies_to_designations"]:
        safeguard_map = _W["missing_safeguards"]["factors"]
        ppp = _W["missing_safeguards"]["points_per_missing"]
        cap = _W["missing_safeguards"]["cap"]

        field_value_pairs = {
            "hi_assessment_completed": rec.hi_assessment_completed,
            "hi_independent_review": rec.hi_independent_review,
            "hi_ongoing_monitoring": rec.hi_ongoing_monitoring,
            "hi_appeal_process": rec.hi_appeal_process,
        }
        for field_name, val in field_value_pairs.items():
            if _safeguard_missing(val):
                label = safeguard_map[field_name]
                contrib = min(ppp, cap - safeguard_pts)
                if contrib > 0:
                    safeguard_pts += contrib
                    safeguard_details.append({
                        "factor": f"Missing safeguard: {label}",
                        "points": contrib,
                        "source_field": field_name,
                        "source_value": val,
                        "rationale": f"{label} not in place for a {rec.hi_designation} use case.",
                    })
            elif val is None:
                missing_fields.append(field_name)

        factors.extend(safeguard_details)

    # ------------------------------------------------------------------
    # 6. Waiver from minimum practices
    # ------------------------------------------------------------------
    if _is_yes(rec.waiver_requested):
        factors.append({
            "factor": "Waiver from minimum risk management practices",
            "points": _W["waiver"]["points"],
            "source_field": "waiver_requested",
            "source_value": rec.waiver_requested,
            "rationale": "Agency requested an extension/waiver from minimum OMB risk management practices.",
        })
    elif rec.waiver_requested is None and rec.inventory_year == "2024":
        missing_fields.append("waiver_requested")

    # ------------------------------------------------------------------
    # 7. External evidence (Phase 4 — optional)
    # ------------------------------------------------------------------
    if external_evidence:
        ev_pts_map = {
            "litigation": _W["external_evidence"]["litigation"],
            "gao": _W["external_evidence"]["gao_ig_finding"],
            "ig": _W["external_evidence"]["gao_ig_finding"],
            "other": _W["external_evidence"]["other"],
        }
        for ev in external_evidence:
            ev_type = (ev.get("type") or "other").lower()
            pts = ev_pts_map.get(ev_type, _W["external_evidence"]["other"])
            factors.append({
                "factor": f"External evidence: {ev_type}",
                "points": pts,
                "source_field": "known_issues.json",
                "source_value": ev.get("title", ""),
                "rationale": f"Known {ev_type} issue: {ev.get('title', '')} ({ev.get('date', 'date unknown')})",
            })

    # ------------------------------------------------------------------
    # Total and band
    # ------------------------------------------------------------------
    raw_total = sum(f["points"] for f in factors)
    total = min(raw_total, _W["score_cap"])

    # Sort factors descending by points
    factors.sort(key=lambda x: x["points"], reverse=True)

    return {
        "record_id": rec.record_id,
        "total": total,
        "band": _band(total),
        "factors": factors,
        "missing_fields": sorted(set(missing_fields)),
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Batch scorer (returns all scored records, sorted by total desc)
# ---------------------------------------------------------------------------

def score_all(
    records: list[NormalizedRecord],
    external_evidence_map: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """Score all records. Returns list of score dicts sorted by total desc.

    Args:
        records: List of NormalizedRecord objects.
        external_evidence_map: Optional dict mapping record_id -> list of evidence entries.

    Returns:
        list[dict]: Score results sorted by total descending.
    """
    ev_map = external_evidence_map or {}
    results = [score_record(rec, ev_map.get(rec.record_id)) for rec in records]
    results.sort(key=lambda x: x["total"], reverse=True)
    return results
