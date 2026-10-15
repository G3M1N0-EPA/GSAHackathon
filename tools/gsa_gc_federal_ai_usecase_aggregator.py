"""
GSA Hackathon: Federal AI Use Case Aggregator Tool
Collects all publicly available USA federal government AI use cases from the
official OMB GitHub inventories (2024 and 2025) and consolidates them into
a single output file.

Sources:
  - 2024 OMB Consolidated Inventory (v2):
    https://github.com/ombegov/2024-Federal-AI-Use-Case-Inventory
  - 2025 OMB Individually Reported AI Use Cases:
    https://github.com/ombegov/2025-Federal-Agency-AI-Use-Case-Inventory
  - 2025 OMB COTS AI Use Cases:
    https://github.com/ombegov/2025-Federal-Agency-AI-Use-Case-Inventory
"""

import csv
import io
import json
import os
from datetime import UTC, datetime

import requests
from ibm_watsonx_orchestrate.agent_builder.tools import ToolPermission, tool
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RAW_BASE = "https://raw.githubusercontent.com"

SOURCES = {
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
OUTPUT_DIR = "/tmp"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AIUseCase(BaseModel):
    agency: str
    bureau: str | None = None
    use_case_name: str
    topic_area: str | None = None
    development_stage: str | None = None
    is_rights_impacting: str | None = None
    is_safety_impacting: str | None = None
    purpose_and_benefits: str | None = None
    system_outputs: str | None = None
    is_high_impact: str | None = None
    vendor_name: str | None = None
    inventory_year: str
    source: str
    collected_at: str


class AggregatorResult(BaseModel):
    total_records: int
    agencies_covered: int
    agencies_breakdown: dict
    topic_areas_breakdown: dict
    rights_impacting_count: int
    safety_impacting_count: int
    high_impact_count: int
    output_path: str
    save_format: str
    collected_at: str
    sources_fetched: list
    records_per_source: dict
    sample_records: list
    errors: list


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedup_key(record: AIUseCase) -> str:
    return (
        f"{record.agency.lower().strip()}"
        f"|{record.use_case_name.lower().strip()}"
        f"|{record.inventory_year}"
    )


def _clean(value: str) -> str | None:
    v = (value or "").strip()
    return v if v else None


def _fetch_csv(url: str, errors: list) -> list:
    """Fetch a remote CSV and return list of dicts."""
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)
    except Exception as exc:
        errors.append(f"Fetch failed [{url}]: {exc}")
        return []


# ---------------------------------------------------------------------------
# Source parsers
# ---------------------------------------------------------------------------

def _parse_2024(rows: list, collected_at: str) -> list:
    records = []
    for row in rows:
        agency = _clean(row.get("Agency", ""))
        name = _clean(row.get("Use Case Name", ""))
        if not agency or not name:
            continue
        impact_raw = _clean(row.get("Is the AI use case rights-impacting, safety-impacting, both, or neither?", "")) or ""
        impact_lower = impact_raw.lower()
        records.append(AIUseCase(
            agency=agency,
            bureau=_clean(row.get("Bureau", "")),
            use_case_name=name,
            topic_area=_clean(row.get("Use Case Topic Area", "")),
            development_stage=_clean(row.get("Stage of Development", "")),
            is_rights_impacting="Yes" if "rights" in impact_lower else ("No" if impact_raw else None),
            is_safety_impacting="Yes" if "safety" in impact_lower else ("No" if impact_raw else None),
            purpose_and_benefits=_clean(row.get("What is the intended purpose and expected benefits of the AI?", "")),
            system_outputs=_clean(row.get("Describe the AI system\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffds outputs.", ""))
                or _clean(row.get("Describe the AI system's outputs.", "")),
            is_high_impact=None,
            vendor_name=None,
            inventory_year="2024",
            source="ombegov/2024-Federal-AI-Use-Case-Inventory",
            collected_at=collected_at,
        ))
    return records


def _parse_2025_individual(rows: list, collected_at: str) -> list:
    records = []
    for row in rows:
        agency = _clean(row.get("agency_name", "") or row.get("agency", ""))
        name = _clean(row.get("use_case_name", ""))
        if not agency or not name:
            continue
        records.append(AIUseCase(
            agency=agency,
            bureau=_clean(row.get("agency_bureau", "")),
            use_case_name=name,
            topic_area=_clean(row.get("topic_area", "")),
            development_stage=_clean(row.get("development_stage", "")),
            is_rights_impacting=None,
            is_safety_impacting=None,
            purpose_and_benefits=_clean(row.get("benefits", "") or row.get("problem_solved", "")),
            system_outputs=_clean(row.get("system_outputs", "")),
            is_high_impact=_clean(row.get("is_high_impact", "")),
            vendor_name=_clean(row.get("vendor_name", "")),
            inventory_year="2025",
            source="ombegov/2025-Federal-Agency-AI-Use-Case-Inventory (individual)",
            collected_at=collected_at,
        ))
    return records


def _parse_2025_cots(rows: list, collected_at: str) -> list:
    records = []
    for row in rows:
        agency = _clean(row.get("Agency", ""))
        name = _clean(row.get("AI Use Case", ""))
        if not agency or not name:
            continue
        records.append(AIUseCase(
            agency=agency,
            bureau=None,
            use_case_name=name,
            topic_area="COTS",
            development_stage=None,
            is_rights_impacting=None,
            is_safety_impacting=None,
            purpose_and_benefits=None,
            system_outputs=None,
            is_high_impact=None,
            vendor_name=_clean(row.get("Name of Commercial Product or Service Used", "")),
            inventory_year="2025",
            source="ombegov/2025-Federal-Agency-AI-Use-Case-Inventory (COTS)",
            collected_at=collected_at,
        ))
    return records


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_WRITE)
def gsa_gc_federal_ai_usecase_aggregator(
    save_format: str = "json",
    output_filename: str = "federal_ai_usecases",
) -> AggregatorResult:
    """Aggregate all USA federal government public AI use cases from official OMB inventories into one file.

    Collects the 2024 and 2025 OMB Federal AI Use Case Inventories from the official
    ombegov GitHub repositories, deduplicates, normalises to a common schema, and saves
    the result to a local file.

    Args:
        save_format (str): Output format for the saved file. Must be 'json' or 'csv'. Defaults to 'json'.
        output_filename (str): Base filename without extension for the output file. Defaults to 'federal_ai_usecases'.
    Returns:
        AggregatorResult: Summary containing total records, agencies covered, output path, records per source, and any fetch errors.
    """
    collected_at = datetime.now(UTC).isoformat()
    errors: list = []
    sources_fetched: list = []
    records_per_source: dict = {}

    # --- 2024 inventory ---
    rows_2024 = _fetch_csv(SOURCES["2024_inventory"], errors)
    parsed_2024 = _parse_2024(rows_2024, collected_at)
    if parsed_2024:
        sources_fetched.append("2024_inventory")
        records_per_source["2024_inventory"] = len(parsed_2024)

    # --- 2025 individually reported ---
    rows_2025_ind = _fetch_csv(SOURCES["2025_individual"], errors)
    parsed_2025_ind = _parse_2025_individual(rows_2025_ind, collected_at)
    if parsed_2025_ind:
        sources_fetched.append("2025_individual")
        records_per_source["2025_individual"] = len(parsed_2025_ind)

    # --- 2025 COTS ---
    rows_2025_cots = _fetch_csv(SOURCES["2025_cots"], errors)
    parsed_2025_cots = _parse_2025_cots(rows_2025_cots, collected_at)
    if parsed_2025_cots:
        sources_fetched.append("2025_cots")
        records_per_source["2025_cots"] = len(parsed_2025_cots)

    all_records = parsed_2024 + parsed_2025_ind + parsed_2025_cots

    # Deduplicate
    seen: dict = {}
    for record in all_records:
        key = _dedup_key(record)
        if key not in seen:
            seen[key] = record
    deduped = list(seen.values())

    # Save output
    ext = "csv" if save_format.lower() == "csv" else "json"
    output_path = os.path.join(OUTPUT_DIR, f"{output_filename}.{ext}")

    if ext == "json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump() for r in deduped], f, indent=2, ensure_ascii=False)
    else:
        fieldnames = list(AIUseCase.model_fields.keys())
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in deduped:
                writer.writerow(r.model_dump())

    # --- Compute breakdowns for agent reasoning ---
    agencies_breakdown: dict = {}
    topic_areas_breakdown: dict = {}
    rights_impacting_count = 0
    safety_impacting_count = 0
    high_impact_count = 0

    for r in deduped:
        agencies_breakdown[r.agency] = agencies_breakdown.get(r.agency, 0) + 1
        ta = r.topic_area or "Unknown"
        topic_areas_breakdown[ta] = topic_areas_breakdown.get(ta, 0) + 1
        if (r.is_rights_impacting or "").lower() == "yes":
            rights_impacting_count += 1
        if (r.is_safety_impacting or "").lower() == "yes":
            safety_impacting_count += 1
        if (r.is_high_impact or "").lower() in ("yes", "true", "1"):
            high_impact_count += 1

    # Sort breakdowns descending by count
    agencies_breakdown = dict(
        sorted(agencies_breakdown.items(), key=lambda x: x[1], reverse=True)
    )
    topic_areas_breakdown = dict(
        sorted(topic_areas_breakdown.items(), key=lambda x: x[1], reverse=True)
    )

    return AggregatorResult(
        total_records=len(deduped),
        agencies_covered=len(agencies_breakdown),
        agencies_breakdown=agencies_breakdown,
        topic_areas_breakdown=topic_areas_breakdown,
        rights_impacting_count=rights_impacting_count,
        safety_impacting_count=safety_impacting_count,
        high_impact_count=high_impact_count,
        output_path=output_path,
        save_format=ext,
        collected_at=collected_at,
        sources_fetched=sources_fetched,
        records_per_source=records_per_source,
        sample_records=[r.model_dump() for r in deduped[:20]],
        errors=errors,
    )
