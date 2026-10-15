"""
GSA Hackathon: External Evidence and Risk Propagation Tools

Two tools:
  - gsa_gc_search_litigation   -- CourtListener REST API search (unconfirmed leads)
  - gsa_gc_risk_propagation    -- propagate known issues to similar use cases at other agencies

Phase 4 of the litigation-risk indicator system.
"""

import json
import os
from pathlib import Path
from typing import Any, List, Optional

import requests
from pydantic import BaseModel

from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission
from _data_loader import load_records, get_record_by_id
from _scorer import score_record
from _embeddings import (
    load_index,
    get_index,
    cosine_similarity_single,
    index_loaded,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COURTLISTENER_BASE = "https://www.courtlistener.com/api/rest/v4"
_REQUEST_TIMEOUT = 10  # seconds — explicit timeout on every network call

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_KNOWN_ISSUES_PATH = _DATA_DIR / "known_issues.json"

# Bootstrap index (may already be loaded by similarity module)
if not index_loaded():
    load_index()


# ---------------------------------------------------------------------------
# known_issues helpers
# ---------------------------------------------------------------------------

def _load_known_issues() -> list[dict]:
    """Return all confirmed entries from known_issues.json (strips schema comment objects)."""
    if not _KNOWN_ISSUES_PATH.exists():
        return []
    try:
        raw = json.loads(_KNOWN_ISSUES_PATH.read_text())
        return [e for e in raw if e.get("use_case_id") and not e.get("_schema_comment")]
    except Exception:
        return []


def _ev_map() -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for e in _load_known_issues():
        result.setdefault(e["use_case_id"], []).append(e)
    return result


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LitigationCase(BaseModel):
    case_name: str
    court: str
    date_filed: Optional[str] = None
    docket_url: str
    nature_of_suit: Optional[str] = None
    snippet: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


class LitigationResult(BaseModel):
    query: str
    count: int
    cases: List[Any]
    token_present: bool
    warning: Optional[str] = None
    disclaimer: str

    model_config = {"arbitrary_types_allowed": True}


class PropagatedCase(BaseModel):
    record_id: str
    agency: str
    use_case_name: str
    inventory_year: str
    topic_area: Optional[str]
    similarity: float
    risk_band: str
    missing_safeguards: List[str]
    why_flagged: str

    model_config = {"arbitrary_types_allowed": True}


class PropagationResult(BaseModel):
    source_record_id: str
    source_agency: str
    source_use_case_name: str
    known_issues: List[Any]
    propagated_to: List[Any]
    index_available: bool
    disclaimer: str
    error: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


_DISCLAIMER = (
    "Indicator for triage only, based on agency self-reported data. "
    "CourtListener matches are unconfirmed leads — name similarity does not "
    "prove legal relevance. Not legal advice."
)

_SAFEGUARD_FIELD_LABELS = {
    "hi_assessment_completed": "no impact assessment",
    "hi_independent_review": "no independent review",
    "hi_ongoing_monitoring": "no ongoing monitoring",
    "hi_appeal_process": "no notice/appeal mechanism",
}


# ---------------------------------------------------------------------------
# Tool 1: search_litigation
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_search_litigation(query: str, limit: int = 10) -> LitigationResult:
    """Search CourtListener for federal court cases related to a query.

    Uses the CourtListener REST API to find dockets matching the query text.
    Results are unconfirmed leads — a name match does not mean the case is
    related to a specific AI use case. Always verify manually before adding
    to data/known_issues.json.

    Requires the COURTLISTENER_API_TOKEN environment variable. If absent,
    returns a clear message instead of failing.

    Args:
        query (str): Search query, e.g. agency name, system name, or topic.
        limit (int): Maximum number of cases to return. Defaults to 10.
    Returns:
        LitigationResult: List of matching dockets with case name, court, date filed, and URL.
    """
    token = os.environ.get("COURTLISTENER_API_TOKEN", "")
    token_present = bool(token)

    if not token_present:
        return LitigationResult(
            query=query,
            count=0,
            cases=[],
            token_present=False,
            warning=(
                "COURTLISTENER_API_TOKEN environment variable is not set. "
                "Set it to enable live CourtListener searches. "
                "Register at https://www.courtlistener.com/sign-in/ for a free API token."
            ),
            disclaimer=_DISCLAIMER,
        )

    headers = {"Authorization": f"Token {token}"}
    params = {
        "q": query,
        "type": "d",       # dockets
        "order_by": "score desc",
        "page_size": min(limit, 20),
    }

    try:
        resp = requests.get(
            f"{_COURTLISTENER_BASE}/dockets/",
            params=params,
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return LitigationResult(
            query=query, count=0, cases=[], token_present=True,
            warning="CourtListener request timed out after 10 seconds.",
            disclaimer=_DISCLAIMER,
        )
    except requests.exceptions.RequestException as exc:
        return LitigationResult(
            query=query, count=0, cases=[], token_present=True,
            warning=f"CourtListener request failed: {exc}",
            disclaimer=_DISCLAIMER,
        )

    data = resp.json()
    results = data.get("results", [])
    cases = []
    for r in results[:limit]:
        court_id = r.get("court_id") or ""
        docket_id = r.get("id", "")
        cases.append({
            "case_name": r.get("case_name") or r.get("case_name_short") or "Unknown",
            "court": court_id,
            "date_filed": r.get("date_filed"),
            "docket_url": f"https://www.courtlistener.com/docket/{docket_id}/",
            "nature_of_suit": r.get("nature_of_suit"),
            "snippet": (r.get("case_name") or "")[:200],
        })

    return LitigationResult(
        query=query,
        count=data.get("count", len(cases)),
        cases=cases,
        token_present=True,
        disclaimer=_DISCLAIMER,
    )


# ---------------------------------------------------------------------------
# Tool 2: risk_propagation
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_risk_propagation(
    use_case_id: str,
    k: int = 10,
    min_similarity: float = 0.75,
) -> PropagationResult:
    """Propagate known legal or oversight issues from one use case to similar ones at other agencies.

    For a use case that has entries in data/known_issues.json, finds similar use
    cases at OTHER agencies above min_similarity and flags them, showing which
    safeguards they lack. Includes a plain-language 'why flagged' explanation
    built entirely from the data (no LLM).

    Only use cases with confirmed entries in known_issues.json trigger propagation.
    Use gsa_gc_search_litigation to find candidates, then manually curate
    known_issues.json before using this tool.

    Args:
        use_case_id (str): The record_id of the source use case with known issues.
        k (int): Maximum number of similar use cases to return. Defaults to 10.
        min_similarity (float): Minimum cosine similarity threshold (0-1). Defaults to 0.75.
    Returns:
        PropagationResult: Similar use cases at other agencies, each with similarity, risk band, missing safeguards, and a plain-language explanation.
    """
    src_rec = get_record_by_id(use_case_id)
    if src_rec is None:
        return PropagationResult(
            source_record_id=use_case_id,
            source_agency="",
            source_use_case_name="",
            known_issues=[],
            propagated_to=[],
            index_available=index_loaded(),
            disclaimer=_DISCLAIMER,
            error=f"record_id '{use_case_id}' not found in inventory.",
        )

    ev_map = _ev_map()
    source_issues = ev_map.get(use_case_id, [])

    if not source_issues:
        return PropagationResult(
            source_record_id=use_case_id,
            source_agency=src_rec.agency,
            source_use_case_name=src_rec.use_case_name,
            known_issues=[],
            propagated_to=[],
            index_available=index_loaded(),
            disclaimer=_DISCLAIMER,
            error=(
                f"No entries found in data/known_issues.json for '{use_case_id}'. "
                "Add confirmed entries before running propagation."
            ),
        )

    if not index_loaded():
        return PropagationResult(
            source_record_id=use_case_id,
            source_agency=src_rec.agency,
            source_use_case_name=src_rec.use_case_name,
            known_issues=source_issues,
            propagated_to=[],
            index_available=False,
            disclaimer=_DISCLAIMER,
            error="Embedding index not loaded. Run `python scripts/build_index.py` first.",
        )

    idx_map = get_index()["id_to_idx"]
    matrix = get_index()["matrix"]
    ids = get_index()["ids"]

    if use_case_id not in idx_map:
        return PropagationResult(
            source_record_id=use_case_id,
            source_agency=src_rec.agency,
            source_use_case_name=src_rec.use_case_name,
            known_issues=source_issues,
            propagated_to=[],
            index_available=True,
            disclaimer=_DISCLAIMER,
            error=f"'{use_case_id}' not in embedding index (was the index rebuilt after this record was added?).",
        )

    query_vec = matrix[idx_map[use_case_id]]
    sims = cosine_similarity_single(query_vec, matrix)

    import numpy as np
    order = np.argsort(sims)[::-1]

    issue_types = sorted({e.get("type", "other") for e in source_issues})
    issue_titles = [e.get("title", "") for e in source_issues if e.get("title")]

    propagated: list[dict] = []
    for i in order:
        sim = float(sims[i])
        if sim < min_similarity:
            break
        rid = ids[i]
        if rid == use_case_id:
            continue
        rec = get_record_by_id(rid)
        if rec is None:
            continue
        if rec.agency == src_rec.agency:
            continue  # only flag OTHER agencies

        # Score to get band and missing safeguards
        score_result = score_record(rec, ev_map.get(rid))
        band = score_result["band"]
        missing = score_result.get("missing_fields", [])

        # Plain-language "why flagged" from data only (no LLM)
        safeguard_labels = [
            _SAFEGUARD_FIELD_LABELS[f]
            for f in missing
            if f in _SAFEGUARD_FIELD_LABELS
        ]
        issue_type_str = " and ".join(issue_types) if issue_types else "oversight"
        safeguard_str = (
            f"; lacks: {', '.join(safeguard_labels)}" if safeguard_labels else ""
        )
        why_flagged = (
            f"Similar ({sim:.0%}) to {src_rec.agency}'s '{src_rec.use_case_name}', "
            f"which has a known {issue_type_str} issue "
            f"({issue_titles[0][:60] + '...' if issue_titles and len(issue_titles[0]) > 60 else (issue_titles[0] if issue_titles else 'see known_issues.json')})"
            f"{safeguard_str}."
        )

        propagated.append({
            "record_id": rid,
            "agency": rec.agency,
            "use_case_name": rec.use_case_name,
            "inventory_year": rec.inventory_year,
            "topic_area": rec.topic_area,
            "similarity": round(sim, 4),
            "risk_band": band,
            "missing_safeguards": safeguard_labels,
            "why_flagged": why_flagged,
        })

        if len(propagated) >= k:
            break

    # Compact known_issues for the response (strip schema comment objects)
    clean_issues = [
        {k: v for k, v in e.items() if not k.startswith("_")}
        for e in source_issues
    ]

    return PropagationResult(
        source_record_id=use_case_id,
        source_agency=src_rec.agency,
        source_use_case_name=src_rec.use_case_name,
        known_issues=clean_issues,
        propagated_to=propagated,
        index_available=True,
        disclaimer=_DISCLAIMER,
    )
