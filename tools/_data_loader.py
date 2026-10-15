"""
Shared data loader for the GSA Federal AI Use Case tools.

Fetches the OMB 2024 and 2025 inventories once and caches them in memory for
the lifetime of the process. All tools import from this module — never write a
second loader.

Cache keys:
  _CACHE["records"]  -> list[NormalizedRecord]
  _CACHE["loaded_at"] -> ISO-8601 string
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RAW_BASE = "https://raw.githubusercontent.com"

_SOURCES: dict[str, str] = {
    "2024_inventory": (
        f"{RAW_BASE}/ombegov/2024-Federal-AI-Use-Case-Inventory"
        "/main/data/2024_consolidated_ai_inventory_raw_v2.csv"
    ),
    "2025_individual": (
        f"{RAW_BASE}/ombegov/2025-Federal-Agency-AI-Use-Case-Inventory"
        "/main/Data/2025_individually_reported_AI_use_cases.csv"
    ),
    "2025_cots": (
        f"{RAW_BASE}/ombegov/2025-Federal-Agency-AI-Use-Case-Inventory"
        "/main/Data/2025_consolidated_COTS_AI_use_cases.csv"
    ),
}

REQUEST_TIMEOUT = 60

# ---------------------------------------------------------------------------
# High-impact designation enum (explicit mapping — never use substring match)
# ---------------------------------------------------------------------------

HI_NOT_HIGH_IMPACT = "NOT_HIGH_IMPACT"
HI_HIGH_IMPACT = "HIGH_IMPACT"
HI_PRESUMED_NOT_HIGH_IMPACT = "PRESUMED_NOT_HIGH_IMPACT"
HI_UNKNOWN = "UNKNOWN"

# Exact string → canonical designation.
# ALL distinct values observed in the 2025 inventory are listed here.
_HI_EXACT_MAP: dict[str, str] = {
    "High-impact": HI_HIGH_IMPACT,
    "Not High-impact": HI_NOT_HIGH_IMPACT,
    "Presumed High-Impact, but Not High-impact": HI_PRESUMED_NOT_HIGH_IMPACT,
    "": HI_UNKNOWN,
}

# Development-stage canonical values
STAGE_PRE_DEPLOYMENT = "pre_deployment"
STAGE_PILOT = "pilot"
STAGE_DEPLOYED = "deployed"
STAGE_RETIRED = "retired"
STAGE_UNKNOWN = "unknown"

# 2025 individual stage values (exact)
_STAGE_MAP_2025: dict[str, str] = {
    "Pre-deployment": STAGE_PRE_DEPLOYMENT,
    "Pilot": STAGE_PILOT,
    "Deployed": STAGE_DEPLOYED,
    "Retired": STAGE_RETIRED,
}

# 2024 stage values (exact)
_STAGE_MAP_2024: dict[str, str] = {
    "Initiated": STAGE_PRE_DEPLOYMENT,
    "Planned": STAGE_PRE_DEPLOYMENT,
    "Ideation": STAGE_PRE_DEPLOYMENT,
    "Acquisition and/or Development": STAGE_PRE_DEPLOYMENT,
    "Implementation and Assessment": STAGE_PILOT,
    "Operation and Maintenance": STAGE_DEPLOYED,
    "In production": STAGE_DEPLOYED,
    "In mission": STAGE_DEPLOYED,
    "Research or  Administrative Action Complete": STAGE_DEPLOYED,
    "Retired": STAGE_RETIRED,
}

# ---------------------------------------------------------------------------
# Normalised record
# ---------------------------------------------------------------------------

@dataclass
class NormalizedRecord:
    """Single canonical AI use-case record across all inventory years."""

    # --- Identity ---
    record_id: str                          # stable synthetic ID
    agency: str
    bureau: str | None
    use_case_name: str
    inventory_year: str                     # "2024" | "2025"
    source: str                             # source key

    # --- Description ---
    topic_area: str | None
    development_stage: str                  # canonical STAGE_* constant
    purpose_and_benefits: str | None
    system_outputs: str | None

    # --- Risk fields ---
    hi_designation: str                     # canonical HI_* constant
    hi_justification: str | None
    is_rights_impacting: str | None      # "Yes" | "No" | None
    is_safety_impacting: str | None      # "Yes" | "No" | None
    has_pii: str | None                  # "Yes" | "No" | None
    waiver_requested: str | None         # "Yes" | "No" | None

    # --- Safeguard fields (only populated for 2025 HI cases) ---
    hi_assessment_completed: str | None
    hi_independent_review: str | None
    hi_ongoing_monitoring: str | None
    hi_appeal_process: str | None

    # --- Vendor ---
    vendor_name: str | None
    is_cots: bool

    # --- Raw row (for debugging) ---
    raw: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_CACHE: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(value: str) -> str | None:
    v = (value or "").strip()
    return v if v else None


def _slugify(text: str) -> str:
    """Return a filesystem-safe slug for synthetic IDs."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    return text[:60]


def _map_hi(raw: str) -> str:
    """Map raw is_high_impact string to canonical designation.

    Uses exact matching only — never substring matching.
    Unknown values fall back to HI_UNKNOWN.
    """
    v = (raw or "").strip()
    return _HI_EXACT_MAP.get(v, HI_UNKNOWN)


def _map_stage(raw: str, year: str) -> str:
    v = (raw or "").strip()
    if year == "2025":
        return _STAGE_MAP_2025.get(v, STAGE_UNKNOWN)
    return _STAGE_MAP_2024.get(v, STAGE_UNKNOWN)


def _parse_rights_safety(raw: str) -> tuple[str | None, str | None]:
    """Return (is_rights_impacting, is_safety_impacting) from 2024 combined field."""
    v = (raw or "").strip().lower()
    if not v:
        return None, None
    rights = "Yes" if "rights" in v else ("No" if v else None)
    safety = "Yes" if "safety" in v else ("No" if v else None)
    return rights, safety


def _yes_no(raw: str) -> str | None:
    v = (raw or "").strip().lower()
    if v in ("yes", "y", "true", "1"):
        return "Yes"
    if v in ("no", "n", "false", "0", "n/a", "not applicable", "na"):
        return "No"
    return None


def _fetch_csv(url: str) -> list[dict]:
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_2024(rows: list[dict]) -> list[NormalizedRecord]:
    records = []
    for row in rows:
        agency = _clean(row.get("Agency", "")) or "Unknown Agency"
        abbrev = _clean(row.get("Agency Abbreviation", "")) or _slugify(agency)
        name = _clean(row.get("Use Case Name", ""))
        if not name:
            continue
        record_id = f"{abbrev}|{_slugify(name)}|2024"
        rights_raw = row.get(
            "Is the AI use case rights-impacting, safety-impacting, both, or neither?", ""
        )
        rights, safety = _parse_rights_safety(rights_raw)
        pii_raw = row.get(
            "Does this AI use case involve personally identifiable information (PII) that is maintained by the agency?",
            "",
        )
        waiver_raw = row.get(
            "Has your agency requested an extension to implement the minimum risk management practices for this AI use case?",
            "",
        )
        waiver = (
            "Yes"
            if "yes" in (waiver_raw or "").lower() and "agency requested" in (waiver_raw or "").lower()
            else None
        )
        assessment_raw = row.get("Has an AI impact assessment been conducted for this AI use case?", "")
        independent_raw = row.get("Has an independent evaluation of the AI use case been conducted?", "")
        monitoring_raw = row.get(
            "Is there a process to monitor performance of the AI system\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffds functionality and changes to its impact on rights or safety as part of the post-deployment plan for the AI use case?",
            "",
        ) or row.get(
            "Is there a process to monitor performance of the AI system's functionality and changes to its impact on rights or safety as part of the post-deployment plan for the AI use case?",
            "",
        )
        appeal_raw = row.get(
            "Is there an established fallback and escalation process for this AI use case in the event that an impacted individual or group would like to appeal or contest the AI system\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffds outcome?",
            "",
        ) or row.get(
            "Is there an established fallback and escalation process for this AI use case in the event that an impacted individual or group would like to appeal or contest the AI system's outcome?",
            "",
        )

        records.append(
            NormalizedRecord(
                record_id=record_id,
                agency=agency,
                bureau=_clean(row.get("Bureau", "")),
                use_case_name=name,
                inventory_year="2024",
                source="2024_inventory",
                topic_area=_clean(row.get("Use Case Topic Area", "")),
                development_stage=_map_stage(row.get("Stage of Development", ""), "2024"),
                purpose_and_benefits=_clean(
                    row.get("What is the intended purpose and expected benefits of the AI?", "")
                ),
                system_outputs=_clean(row.get("Describe the AI system\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffds outputs.", ""))
                    or _clean(row.get("Describe the AI system's outputs.", "")),
                hi_designation=HI_UNKNOWN,   # 2024 has no high-impact field
                hi_justification=None,
                is_rights_impacting=rights,
                is_safety_impacting=safety,
                has_pii=_yes_no(pii_raw),
                waiver_requested=waiver,
                hi_assessment_completed=_yes_no(assessment_raw),
                hi_independent_review=_yes_no(independent_raw),
                hi_ongoing_monitoring=_yes_no(monitoring_raw),
                hi_appeal_process=_yes_no(appeal_raw),
                vendor_name=None,
                is_cots=False,
                raw=dict(row),
            )
        )
    return records


def _parse_2025_individual(rows: list[dict]) -> list[NormalizedRecord]:
    records = []
    for row in rows:
        agency = _clean(row.get("agency_name", "") or row.get("agency", "")) or "Unknown Agency"
        abbrev = _clean(row.get("agency", "")) or _slugify(agency)
        name = _clean(row.get("use_case_name", ""))
        if not name:
            continue
        row_id = _clean(row.get("id", ""))
        record_id = row_id if row_id else f"{abbrev}|{_slugify(name)}|2025"

        # hi_ongoing_monitoring can be long free text — normalise to Yes/No/None
        monitoring_raw = _clean(row.get("hi_ongoing_monitoring", "")) or ""
        if monitoring_raw.lower().startswith("yes"):
            monitoring_norm: str | None = "Yes"
        elif monitoring_raw.lower().startswith("no"):
            monitoring_norm = "No"
        elif monitoring_raw.lower() == "in-progress":
            monitoring_norm = "In-progress"
        else:
            monitoring_norm = _clean(monitoring_raw) if monitoring_raw else None

        # hi_independent_review: map CAIO Waived as a notable value
        independent_raw = _clean(row.get("hi_independent_review", "")) or ""

        records.append(
            NormalizedRecord(
                record_id=record_id,
                agency=agency,
                bureau=_clean(row.get("agency_bureau", "")),
                use_case_name=name,
                inventory_year="2025",
                source="2025_individual",
                topic_area=_clean(row.get("topic_area", "")),
                development_stage=_map_stage(row.get("development_stage", ""), "2025"),
                purpose_and_benefits=_clean(row.get("benefits", "") or row.get("problem_solved", "")),
                system_outputs=_clean(row.get("system_outputs", "")),
                hi_designation=_map_hi(row.get("is_high_impact", "")),
                hi_justification=_clean(row.get("HI_justification", "")),
                is_rights_impacting=None,   # not present in 2025 individual
                is_safety_impacting=None,
                has_pii=_yes_no(row.get("has_pii", "")),
                waiver_requested=None,      # not present in 2025 individual
                hi_assessment_completed=_yes_no(row.get("hi_assessment_completed", "")),
                hi_independent_review=_clean(independent_raw) if independent_raw else None,
                hi_ongoing_monitoring=monitoring_norm,
                hi_appeal_process=_clean(row.get("hi_appeal_process", "")),
                vendor_name=_clean(row.get("vendor_name", "")),
                is_cots=False,
                raw=dict(row),
            )
        )
    return records


def _parse_2025_cots(rows: list[dict]) -> list[NormalizedRecord]:
    records = []
    for row in rows:
        agency = _clean(row.get("Agency", "")) or "Unknown Agency"
        name = _clean(row.get("AI Use Case", ""))
        if not name:
            continue
        record_id = f"COTS|{_slugify(agency)}|{_slugify(name)}|2025"
        records.append(
            NormalizedRecord(
                record_id=record_id,
                agency=agency,
                bureau=None,
                use_case_name=name,
                inventory_year="2025",
                source="2025_cots",
                topic_area="COTS",
                development_stage=STAGE_UNKNOWN,
                purpose_and_benefits=None,
                system_outputs=None,
                hi_designation=HI_UNKNOWN,
                hi_justification=None,
                is_rights_impacting=None,
                is_safety_impacting=None,
                has_pii=None,
                waiver_requested=None,
                hi_assessment_completed=None,
                hi_independent_review=None,
                hi_ongoing_monitoring=None,
                hi_appeal_process=None,
                vendor_name=_clean(row.get("Name of Commercial Product or Service Used", "")),
                is_cots=True,
                raw=dict(row),
            )
        )
    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_records(force_reload: bool = False) -> list[NormalizedRecord]:
    """Return cached list of all normalised records, fetching if needed.

    Args:
        force_reload: If True, bypass cache and re-fetch from GitHub.

    Returns:
        list[NormalizedRecord]: All deduplicated records across all inventory years.
    """
    if not force_reload and _CACHE.get("records") is not None:
        return _CACHE["records"]

    records_2024 = _parse_2024(_fetch_csv(_SOURCES["2024_inventory"]))
    records_2025_ind = _parse_2025_individual(_fetch_csv(_SOURCES["2025_individual"]))
    records_2025_cots = _parse_2025_cots(_fetch_csv(_SOURCES["2025_cots"]))

    all_records = records_2024 + records_2025_ind + records_2025_cots

    # Deduplicate by record_id — first occurrence wins
    seen: dict[str, NormalizedRecord] = {}
    for rec in all_records:
        if rec.record_id not in seen:
            seen[rec.record_id] = rec

    deduped = list(seen.values())
    _CACHE["records"] = deduped
    _CACHE["loaded_at"] = datetime.now(UTC).isoformat()
    return deduped


def get_record_by_id(record_id: str) -> NormalizedRecord | None:
    """Return a single record by its record_id, or None if not found."""
    for rec in load_records():
        if rec.record_id == record_id:
            return rec
    return None


def loaded_at() -> str | None:
    """Return ISO-8601 timestamp of when the cache was last populated."""
    return _CACHE.get("loaded_at")
