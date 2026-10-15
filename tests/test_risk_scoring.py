"""
pytest suite for the litigation-risk scoring engine.

Tests cover:
  - One fixture use case per band (Low, Moderate, Elevated)
  - PRESUMED_NOT_HIGH_IMPACT is NOT counted as HIGH_IMPACT
  - Missing fields do not crash scoring
  - Waiver adds correct points
  - External evidence adds correct points
  - Score is always capped at 100
  - Band boundaries are respected
  - score_all() returns results sorted descending
  - get_record_by_id returns None for unknown ID
"""

from __future__ import annotations

import os
import sys

# Make tools/ importable from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from tools._data_loader import (
    HI_HIGH_IMPACT,
    HI_NOT_HIGH_IMPACT,
    HI_PRESUMED_NOT_HIGH_IMPACT,
    HI_UNKNOWN,
    STAGE_DEPLOYED,
    STAGE_PRE_DEPLOYMENT,
    STAGE_UNKNOWN,
    NormalizedRecord,
    get_record_by_id,
)
from tools._scorer import DISCLAIMER, score_all, score_record

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_record(**kwargs) -> NormalizedRecord:
    """Build a NormalizedRecord with safe defaults for any field not supplied."""
    defaults = dict(
        record_id="TEST-001",
        agency="Test Agency",
        bureau=None,
        use_case_name="Test Use Case",
        inventory_year="2025",
        source="2025_individual",
        topic_area="Science",
        development_stage=STAGE_UNKNOWN,
        purpose_and_benefits=None,
        system_outputs=None,
        hi_designation=HI_NOT_HIGH_IMPACT,
        hi_justification=None,
        is_rights_impacting=None,
        is_safety_impacting=None,
        has_pii=None,
        waiver_requested=None,
        hi_assessment_completed=None,
        hi_independent_review=None,
        hi_ongoing_monitoring=None,
        hi_appeal_process=None,
        vendor_name=None,
        is_cots=False,
    )
    defaults.update(kwargs)
    return NormalizedRecord(**defaults)


# ---------------------------------------------------------------------------
# Band fixture tests
# ---------------------------------------------------------------------------

class TestBandFixtures:
    def test_low_band(self):
        """NOT_HIGH_IMPACT, no PII, non-sensitive topic, not deployed → Low."""
        rec = _make_record(
            hi_designation=HI_NOT_HIGH_IMPACT,
            topic_area="Science",
            development_stage=STAGE_PRE_DEPLOYMENT,
            has_pii="No",
        )
        result = score_record(rec)
        assert result["band"] == "Low", f"Expected Low, got {result['band']} (score={result['total']})"
        assert result["total"] < 30

    def test_moderate_band(self):
        """UNKNOWN designation + deployed + PII → should reach Moderate (≥30)."""
        rec = _make_record(
            record_id="TEST-MOD",
            hi_designation=HI_UNKNOWN,          # +10
            topic_area="Benefits Processing",    # +20 (affects individuals)
            development_stage=STAGE_DEPLOYED,    # +10
            has_pii="Yes",                       # +10
        )
        result = score_record(rec)
        assert result["band"] == "Moderate", f"Expected Moderate, got {result['band']} (score={result['total']})"
        assert 30 <= result["total"] <= 59

    def test_elevated_band(self):
        """HIGH_IMPACT + deployed + PII + sensitive topic + waiver → Elevated (≥60)."""
        rec = _make_record(
            record_id="TEST-ELEV",
            hi_designation=HI_HIGH_IMPACT,          # +25
            topic_area="Law Enforcement",            # +20
            development_stage=STAGE_DEPLOYED,        # +10
            has_pii="Yes",                           # +10
            waiver_requested="Yes",                  # +10
        )
        result = score_record(rec)
        assert result["band"] == "Elevated", f"Expected Elevated, got {result['band']} (score={result['total']})"
        assert result["total"] >= 60


# ---------------------------------------------------------------------------
# Designation discrimination tests
# ---------------------------------------------------------------------------

class TestDesignationMapping:
    def test_presumed_not_high_impact_is_not_high_impact(self):
        """PRESUMED_NOT_HIGH_IMPACT must score +20, not +25 (HIGH_IMPACT points)."""
        rec_presumed = _make_record(
            record_id="TEST-PRESUMED",
            hi_designation=HI_PRESUMED_NOT_HIGH_IMPACT,
            topic_area="Science",
            development_stage=STAGE_UNKNOWN,
        )
        rec_high = _make_record(
            record_id="TEST-HIGH",
            hi_designation=HI_HIGH_IMPACT,
            topic_area="Science",
            development_stage=STAGE_UNKNOWN,
        )
        score_presumed = score_record(rec_presumed)
        score_high = score_record(rec_high)

        # Must not be equal — PRESUMED scores lower than HIGH
        assert score_presumed["total"] < score_high["total"], (
            f"PRESUMED_NOT_HIGH_IMPACT ({score_presumed['total']}) should score "
            f"less than HIGH_IMPACT ({score_high['total']})"
        )
        # Designation factor must show the correct designation
        designation_factor = next(
            f for f in score_presumed["factors"] if f["factor"] == "High-impact designation"
        )
        assert designation_factor["source_value"] == HI_PRESUMED_NOT_HIGH_IMPACT
        assert designation_factor["points"] == 20

    def test_high_impact_designation_points(self):
        rec = _make_record(hi_designation=HI_HIGH_IMPACT)
        result = score_record(rec)
        desig = next(f for f in result["factors"] if f["factor"] == "High-impact designation")
        assert desig["points"] == 25

    def test_not_high_impact_designation_zero_points(self):
        rec = _make_record(hi_designation=HI_NOT_HIGH_IMPACT)
        result = score_record(rec)
        desig = next(f for f in result["factors"] if f["factor"] == "High-impact designation")
        assert desig["points"] == 0

    def test_unknown_designation_ten_points(self):
        rec = _make_record(hi_designation=HI_UNKNOWN, inventory_year="2024")
        result = score_record(rec)
        desig = next(f for f in result["factors"] if f["factor"] == "High-impact designation")
        assert desig["points"] == 10


# ---------------------------------------------------------------------------
# Missing fields — no crash tests
# ---------------------------------------------------------------------------

class TestMissingFields:
    def test_all_none_does_not_crash(self):
        """A record with every optional field as None must not raise."""
        rec = _make_record(
            topic_area=None,
            development_stage=STAGE_UNKNOWN,
            has_pii=None,
            waiver_requested=None,
            hi_designation=HI_UNKNOWN,
            hi_assessment_completed=None,
            hi_independent_review=None,
            hi_ongoing_monitoring=None,
            hi_appeal_process=None,
        )
        result = score_record(rec)
        assert isinstance(result["total"], int)
        assert result["band"] in ("Low", "Moderate", "Elevated")
        assert isinstance(result["missing_fields"], list)

    def test_missing_pii_goes_into_missing_fields(self):
        rec = _make_record(has_pii=None)
        result = score_record(rec)
        assert "has_pii" in result["missing_fields"]

    def test_missing_safeguards_only_for_hi_or_presumed(self):
        """Safeguard fields should only be checked for HIGH_IMPACT / PRESUMED_NOT_HIGH_IMPACT."""
        rec_not_hi = _make_record(
            hi_designation=HI_NOT_HIGH_IMPACT,
            hi_assessment_completed=None,
            hi_independent_review=None,
        )
        result = score_record(rec_not_hi)
        safeguard_factors = [f for f in result["factors"] if "safeguard" in f["factor"].lower()]
        assert len(safeguard_factors) == 0, "Safeguard penalties should not apply to NOT_HIGH_IMPACT"

    def test_safeguard_missing_adds_points_for_high_impact(self):
        """When HIGH_IMPACT and all safeguards are 'No', should get the capped penalty."""
        rec = _make_record(
            hi_designation=HI_HIGH_IMPACT,
            hi_assessment_completed="No",
            hi_independent_review="No",
            hi_ongoing_monitoring="No",
            hi_appeal_process="No",
        )
        result = score_record(rec)
        safeguard_pts = sum(f["points"] for f in result["factors"] if "safeguard" in f["factor"].lower())
        assert safeguard_pts == 15, f"Expected 15 (cap), got {safeguard_pts}"


# ---------------------------------------------------------------------------
# Waiver
# ---------------------------------------------------------------------------

class TestWaiver:
    def test_waiver_yes_adds_ten_points(self):
        rec_no_waiver = _make_record(waiver_requested="No")
        rec_waiver = _make_record(record_id="W-2", waiver_requested="Yes")
        score_no = score_record(rec_no_waiver)["total"]
        score_yes = score_record(rec_waiver)["total"]
        assert score_yes == score_no + 10

    def test_waiver_no_adds_zero(self):
        rec = _make_record(waiver_requested="No")
        result = score_record(rec)
        waiver_factors = [f for f in result["factors"] if "waiver" in f["factor"].lower()]
        assert len(waiver_factors) == 0


# ---------------------------------------------------------------------------
# External evidence
# ---------------------------------------------------------------------------

class TestExternalEvidence:
    def test_litigation_adds_fifteen(self):
        rec = _make_record()
        base = score_record(rec)["total"]
        with_ev = score_record(rec, external_evidence=[{"type": "litigation", "title": "Test v. Agency"}])
        assert with_ev["total"] == min(base + 15, 100)

    def test_gao_adds_ten(self):
        rec = _make_record()
        base = score_record(rec)["total"]
        with_ev = score_record(rec, external_evidence=[{"type": "gao", "title": "GAO-25-123"}])
        assert with_ev["total"] == min(base + 10, 100)


# ---------------------------------------------------------------------------
# Score cap
# ---------------------------------------------------------------------------

class TestScoreCap:
    def test_score_never_exceeds_100(self):
        """A use case with every risk factor maxed out must not exceed 100."""
        rec = _make_record(
            hi_designation=HI_HIGH_IMPACT,
            topic_area="Law Enforcement",
            development_stage=STAGE_DEPLOYED,
            has_pii="Yes",
            waiver_requested="Yes",
            hi_assessment_completed="No",
            hi_independent_review="No",
            hi_ongoing_monitoring="No",
            hi_appeal_process="No",
        )
        result = score_record(
            rec,
            external_evidence=[
                {"type": "litigation", "title": "A v. B"},
                {"type": "gao", "title": "GAO-X"},
            ],
        )
        assert result["total"] <= 100, f"Score exceeded cap: {result['total']}"


# ---------------------------------------------------------------------------
# score_all() ordering
# ---------------------------------------------------------------------------

class TestScoreAll:
    def test_results_sorted_descending(self):
        records = [
            _make_record(record_id="R1", hi_designation=HI_NOT_HIGH_IMPACT),
            _make_record(record_id="R2", hi_designation=HI_HIGH_IMPACT,
                         topic_area="Law Enforcement", development_stage=STAGE_DEPLOYED,
                         has_pii="Yes"),
            _make_record(record_id="R3", hi_designation=HI_UNKNOWN),
        ]
        results = score_all(records)
        totals = [r["total"] for r in results]
        assert totals == sorted(totals, reverse=True)

    def test_unknown_id_returns_none(self):
        assert get_record_by_id("__definitely_not_a_real_id__") is None


# ---------------------------------------------------------------------------
# Disclaimer presence
# ---------------------------------------------------------------------------

class TestDisclaimer:
    def test_disclaimer_always_present(self):
        rec = _make_record()
        result = score_record(rec)
        assert "disclaimer" in result
        assert len(result["disclaimer"]) > 10
        assert result["disclaimer"] == DISCLAIMER
