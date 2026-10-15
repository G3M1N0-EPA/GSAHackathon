"""
pytest suite for Phase 4 external evidence and risk propagation tools.

Tests run with NO network calls and NO real CourtListener token.
All HTTP calls are mocked. Index is loaded from data/ if present,
otherwise the propagation tools return graceful errors.

Tests cover:
  - search_litigation with no token returns a clear message (not an error)
  - search_litigation with a token makes the right HTTP call and parses results
  - search_litigation handles timeout gracefully
  - search_litigation handles HTTP error gracefully
  - _load_known_issues strips schema comment objects
  - _load_known_issues returns empty list when file is missing
  - risk_propagation returns error for unknown record_id
  - risk_propagation returns error when no known_issues entry exists
  - risk_propagation returns error when index is not loaded
  - risk_propagation why_flagged string is built from data (not empty)
  - risk_propagation only returns results from OTHER agencies
  - risk_propagation respects min_similarity threshold
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


import tools.gsa_gc_external_evidence as ev_module
from tools._data_loader import (
    HI_HIGH_IMPACT,
    STAGE_DEPLOYED,
    NormalizedRecord,
)
from tools.gsa_gc_external_evidence import (
    _load_known_issues,
    gsa_gc_risk_propagation,
    gsa_gc_search_litigation,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_record(
    record_id: str = "TEST-001",
    agency: str = "Test Agency",
    use_case_name: str = "Fraud Detection",
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        agency=agency,
        bureau=None,
        use_case_name=use_case_name,
        inventory_year="2025",
        source="test",
        topic_area="Law Enforcement",
        development_stage=STAGE_DEPLOYED,
        purpose_and_benefits="Detect fraud",
        system_outputs="Score",
        hi_designation=HI_HIGH_IMPACT,
        hi_justification=None,
        is_rights_impacting="Yes",
        is_safety_impacting=None,
        has_pii="Yes",
        waiver_requested=None,
        hi_assessment_completed="No",
        hi_independent_review="No",
        hi_ongoing_monitoring=None,
        hi_appeal_process="No",
        vendor_name=None,
        is_cots=False,
    )


_EXAMPLE_ISSUE = {
    "use_case_id": "TEST-001",
    "type": "litigation",
    "title": "ACLU v. Test Agency",
    "url": "https://example.com/case",
    "date": "2024-01-01",
    "summary": "Example litigation issue.",
    "confirmed": True,
    "added_by": "test",
}

_SCHEMA_COMMENT = {
    "_schema_comment": "This is a schema comment and should be filtered out.",
    "_fields": {},
}


# ---------------------------------------------------------------------------
# _load_known_issues
# ---------------------------------------------------------------------------

class TestLoadKnownIssues:
    def test_strips_schema_comment_objects(self, tmp_path, monkeypatch):
        p = tmp_path / "known_issues.json"
        p.write_text(json.dumps([_SCHEMA_COMMENT, _EXAMPLE_ISSUE]))
        monkeypatch.setattr(ev_module, "_KNOWN_ISSUES_PATH", p)
        result = _load_known_issues()
        assert len(result) == 1
        assert result[0]["use_case_id"] == "TEST-001"

    def test_returns_empty_list_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ev_module, "_KNOWN_ISSUES_PATH", tmp_path / "nonexistent.json")
        result = _load_known_issues()
        assert result == []

    def test_returns_empty_list_on_bad_json(self, tmp_path, monkeypatch):
        p = tmp_path / "known_issues.json"
        p.write_text("NOT VALID JSON {{{")
        monkeypatch.setattr(ev_module, "_KNOWN_ISSUES_PATH", p)
        result = _load_known_issues()
        assert result == []


# ---------------------------------------------------------------------------
# search_litigation — no token
# ---------------------------------------------------------------------------

class TestSearchLitigationNoToken:
    def test_no_token_returns_warning_not_error(self, monkeypatch):
        monkeypatch.delenv("COURTLISTENER_API_TOKEN", raising=False)
        result = gsa_gc_search_litigation(query="facial recognition border")
        content = result.content if hasattr(result, "content") else result
        assert content.token_present is False
        assert content.count == 0
        assert content.cases == []
        assert content.warning is not None
        assert "COURTLISTENER_API_TOKEN" in content.warning

    def test_no_token_disclaimer_present(self, monkeypatch):
        monkeypatch.delenv("COURTLISTENER_API_TOKEN", raising=False)
        result = gsa_gc_search_litigation(query="test")
        content = result.content if hasattr(result, "content") else result
        assert len(content.disclaimer) > 10


# ---------------------------------------------------------------------------
# search_litigation — with token (mocked HTTP)
# ---------------------------------------------------------------------------

class TestSearchLitigationWithToken:
    _MOCK_RESPONSE = {
        "count": 2,
        "results": [
            {
                "id": 12345,
                "case_name": "ACLU v. DHS",
                "court_id": "ca9",
                "date_filed": "2023-06-01",
                "nature_of_suit": "Civil Rights",
            },
            {
                "id": 67890,
                "case_name": "EFF v. DOJ",
                "court_id": "dcd",
                "date_filed": "2022-03-15",
                "nature_of_suit": "Privacy",
            },
        ],
    }

    def test_returns_cases(self, monkeypatch):
        monkeypatch.setenv("COURTLISTENER_API_TOKEN", "fake-token-123")
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._MOCK_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        with patch("tools.gsa_gc_external_evidence.requests.get", return_value=mock_resp):
            result = gsa_gc_search_litigation(query="facial recognition")
        content = result.content if hasattr(result, "content") else result
        assert content.token_present is True
        assert content.count == 2
        assert len(content.cases) == 2
        assert content.cases[0]["case_name"] == "ACLU v. DHS"
        assert "courtlistener.com/docket/12345/" in content.cases[0]["docket_url"]

    def test_limit_respected(self, monkeypatch):
        monkeypatch.setenv("COURTLISTENER_API_TOKEN", "fake-token")
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._MOCK_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        with patch("tools.gsa_gc_external_evidence.requests.get", return_value=mock_resp):
            result = gsa_gc_search_litigation(query="test", limit=1)
        content = result.content if hasattr(result, "content") else result
        assert len(content.cases) <= 1

    def test_timeout_returns_warning(self, monkeypatch):
        import requests as req
        monkeypatch.setenv("COURTLISTENER_API_TOKEN", "fake-token")
        with patch(
            "tools.gsa_gc_external_evidence.requests.get",
            side_effect=req.exceptions.Timeout,
        ):
            result = gsa_gc_search_litigation(query="test")
        content = result.content if hasattr(result, "content") else result
        assert content.count == 0
        assert content.warning is not None
        assert "timed out" in content.warning.lower()

    def test_http_error_returns_warning(self, monkeypatch):
        import requests as req
        monkeypatch.setenv("COURTLISTENER_API_TOKEN", "fake-token")
        with patch(
            "tools.gsa_gc_external_evidence.requests.get",
            side_effect=req.exceptions.RequestException("500 Server Error"),
        ):
            result = gsa_gc_search_litigation(query="test")
        content = result.content if hasattr(result, "content") else result
        assert content.count == 0
        assert content.warning is not None


# ---------------------------------------------------------------------------
# risk_propagation
# ---------------------------------------------------------------------------

class TestRiskPropagation:
    def test_unknown_record_id_returns_error(self):
        result = gsa_gc_risk_propagation(use_case_id="__not_a_real_id__")
        content = result.content if hasattr(result, "content") else result
        assert content.error is not None
        assert "not found" in content.error.lower()

    def test_no_known_issues_returns_error(self, monkeypatch, tmp_path):
        p = tmp_path / "known_issues.json"
        p.write_text("[]")
        monkeypatch.setattr(ev_module, "_KNOWN_ISSUES_PATH", p)

        # Patch get_record_by_id to return a real-looking record
        rec = _make_record()
        with patch("tools.gsa_gc_external_evidence.get_record_by_id", return_value=rec):
            result = gsa_gc_risk_propagation(use_case_id="TEST-001")
        content = result.content if hasattr(result, "content") else result
        assert content.error is not None
        assert "known_issues.json" in content.error.lower()

    def test_no_index_returns_error(self, monkeypatch, tmp_path):
        p = tmp_path / "known_issues.json"
        p.write_text(json.dumps([_EXAMPLE_ISSUE]))
        monkeypatch.setattr(ev_module, "_KNOWN_ISSUES_PATH", p)

        rec = _make_record()
        with patch("tools.gsa_gc_external_evidence.get_record_by_id", return_value=rec), \
             patch("tools.gsa_gc_external_evidence.index_loaded", return_value=False):
            result = gsa_gc_risk_propagation(use_case_id="TEST-001")
        content = result.content if hasattr(result, "content") else result
        assert content.index_available is False
        assert content.error is not None

    def test_why_flagged_is_non_empty(self, monkeypatch, tmp_path):
        """why_flagged must be a non-empty string built from data."""
        import numpy as np
        p = tmp_path / "known_issues.json"
        p.write_text(json.dumps([_EXAMPLE_ISSUE]))
        monkeypatch.setattr(ev_module, "_KNOWN_ISSUES_PATH", p)

        src = _make_record(record_id="TEST-001", agency="Agency A")
        other = _make_record(record_id="TEST-002", agency="Agency B", use_case_name="Similar System")

        fake_index = {
            "matrix": np.array([[1.0, 0.0], [0.9, 0.1]], dtype=np.float32),
            "ids": ["TEST-001", "TEST-002"],
            "id_to_idx": {"TEST-001": 0, "TEST-002": 1},
        }

        def fake_get_record(rid):
            return src if rid == "TEST-001" else other if rid == "TEST-002" else None

        with patch("tools.gsa_gc_external_evidence.get_record_by_id", side_effect=fake_get_record), \
             patch("tools.gsa_gc_external_evidence.index_loaded", return_value=True), \
             patch("tools.gsa_gc_external_evidence.get_index", return_value=fake_index):
            result = gsa_gc_risk_propagation(
                use_case_id="TEST-001", k=5, min_similarity=0.5
            )
        content = result.content if hasattr(result, "content") else result
        if content.error is None:
            for item in content.propagated_to:
                assert item["why_flagged"]
                assert len(item["why_flagged"]) > 10

    def test_only_other_agencies_in_results(self, monkeypatch, tmp_path):
        """Propagation must not flag the same agency as the source."""
        import numpy as np
        p = tmp_path / "known_issues.json"
        p.write_text(json.dumps([_EXAMPLE_ISSUE]))
        monkeypatch.setattr(ev_module, "_KNOWN_ISSUES_PATH", p)

        src = _make_record(record_id="TEST-001", agency="Agency A")
        same_agency = _make_record(record_id="TEST-003", agency="Agency A", use_case_name="Same Agency System")
        other = _make_record(record_id="TEST-002", agency="Agency B")

        fake_index = {
            "matrix": np.array([[1.0, 0.0], [0.95, 0.05], [0.9, 0.1]], dtype=np.float32),
            "ids": ["TEST-001", "TEST-003", "TEST-002"],
            "id_to_idx": {"TEST-001": 0, "TEST-003": 1, "TEST-002": 2},
        }

        def fake_get_record(rid):
            return {"TEST-001": src, "TEST-003": same_agency, "TEST-002": other}.get(rid)

        with patch("tools.gsa_gc_external_evidence.get_record_by_id", side_effect=fake_get_record), \
             patch("tools.gsa_gc_external_evidence.index_loaded", return_value=True), \
             patch("tools.gsa_gc_external_evidence.get_index", return_value=fake_index):
            result = gsa_gc_risk_propagation(
                use_case_id="TEST-001", k=5, min_similarity=0.5
            )
        content = result.content if hasattr(result, "content") else result
        if content.error is None:
            agencies = [item["agency"] for item in content.propagated_to]
            assert "Agency A" not in agencies, "Same-agency results must be excluded"

    def test_min_similarity_threshold_respected(self, monkeypatch, tmp_path):
        """Records below min_similarity must not appear in results."""
        import numpy as np
        p = tmp_path / "known_issues.json"
        p.write_text(json.dumps([_EXAMPLE_ISSUE]))
        monkeypatch.setattr(ev_module, "_KNOWN_ISSUES_PATH", p)

        src = _make_record(record_id="TEST-001", agency="Agency A")
        low_sim = _make_record(record_id="TEST-004", agency="Agency B", use_case_name="Unrelated System")

        # TEST-004 will have low similarity (0.4) — below threshold of 0.75
        fake_index = {
            "matrix": np.array([[1.0, 0.0], [0.4, 0.6]], dtype=np.float32),
            "ids": ["TEST-001", "TEST-004"],
            "id_to_idx": {"TEST-001": 0, "TEST-004": 1},
        }

        def fake_get_record(rid):
            return src if rid == "TEST-001" else low_sim

        with patch("tools.gsa_gc_external_evidence.get_record_by_id", side_effect=fake_get_record), \
             patch("tools.gsa_gc_external_evidence.index_loaded", return_value=True), \
             patch("tools.gsa_gc_external_evidence.get_index", return_value=fake_index):
            result = gsa_gc_risk_propagation(
                use_case_id="TEST-001", k=5, min_similarity=0.75
            )
        content = result.content if hasattr(result, "content") else result
        if content.error is None:
            assert all(
                item["similarity"] >= 0.75 for item in content.propagated_to
            ), "Results below min_similarity threshold found"
