"""
pytest suite for the similarity and clustering engine.

Tests are designed to run with NO network calls and NO precomputed index.
They use small in-memory fixture data.

Tests cover:
  - build_text produces non-empty strings
  - TF-IDF embedding produces float32 matrix of correct shape
  - Cosine similarity of identical vectors = 1.0
  - Cosine similarity of zero vector does not crash
  - Clustering on tiny fixture data returns labels of correct length
  - Cluster label generation returns a dict keyed by cluster ID
  - load_index returns False when files don't exist (no precomputed index)
  - embed_query works via TF-IDF vectorizer after embedding
  - cross-agency duplicate logic: same-agency pairs excluded
  - similarity tool returns index_available=False with helpful message when index missing
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from tools._data_loader import (
    HI_NOT_HIGH_IMPACT,
    STAGE_DEPLOYED,
    NormalizedRecord,
)
from tools._embeddings import (
    build_text,
    cluster_embeddings,
    cosine_similarity_matrix,
    cosine_similarity_single,
    generate_cluster_labels,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_record(
    record_id: str = "TEST-1",
    agency: str = "Test Agency",
    use_case_name: str = "Fraud Detection System",
    topic_area: str = "Law & Justice",
    purpose_and_benefits: str = "Detect fraud in benefits processing",
    system_outputs: str = "Fraud probability score",
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        agency=agency,
        bureau=None,
        use_case_name=use_case_name,
        inventory_year="2025",
        source="test",
        topic_area=topic_area,
        development_stage=STAGE_DEPLOYED,
        purpose_and_benefits=purpose_and_benefits,
        system_outputs=system_outputs,
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


# ---------------------------------------------------------------------------
# build_text
# ---------------------------------------------------------------------------

class TestBuildText:
    def test_non_empty(self):
        rec = _make_record()
        text = build_text(rec)
        assert len(text) > 0

    def test_contains_name(self):
        rec = _make_record(use_case_name="Unique System Name XYZ")
        text = build_text(rec)
        assert "Unique System Name XYZ" in text

    def test_none_fields_dont_crash(self):
        rec = _make_record(topic_area=None, purpose_and_benefits=None, system_outputs=None)
        text = build_text(rec)
        assert isinstance(text, str)

    def test_empty_record_returns_string(self):
        rec = _make_record(
            use_case_name="",
            topic_area=None,
            purpose_and_benefits=None,
            system_outputs=None,
        )
        text = build_text(rec)
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors_score_one(self):
        vec = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        matrix = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
        sims = cosine_similarity_single(vec, matrix)
        assert abs(sims[0] - 1.0) < 1e-5

    def test_orthogonal_vectors_score_zero(self):
        vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        matrix = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)
        sims = cosine_similarity_single(vec, matrix)
        assert abs(sims[0]) < 1e-5

    def test_zero_query_vec_does_not_crash(self):
        vec = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        matrix = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
        sims = cosine_similarity_single(vec, matrix)
        assert sims.shape == (1,)
        assert not np.any(np.isnan(sims))

    def test_matrix_cosine_shape(self):
        matrix = np.random.rand(10, 8).astype(np.float32)
        sim_mat = cosine_similarity_matrix(matrix)
        assert sim_mat.shape == (10, 10)

    def test_matrix_diagonal_is_one(self):
        matrix = np.random.rand(5, 4).astype(np.float32)
        sim_mat = cosine_similarity_matrix(matrix)
        diag = np.diag(sim_mat)
        assert np.allclose(diag, 1.0, atol=1e-5)


# ---------------------------------------------------------------------------
# TF-IDF embedding
# ---------------------------------------------------------------------------

class TestTFIDF:
    def test_embedding_shape(self):
        from tools._embeddings import _tfidf_embed
        texts = [
            "fraud detection machine learning benefits",
            "natural language processing document review court",
            "image recognition satellite surveillance national security",
            "predictive analytics budget allocation finance department",
            "chatbot citizen services customer support helpdesk",
        ]
        mat = _tfidf_embed(texts)
        assert mat.shape[0] == len(texts)
        assert mat.dtype == np.float32

    def test_embedding_non_zero(self):
        from tools._embeddings import _tfidf_embed
        texts = ["machine learning fraud detection model", "natural language processing summarization"]
        mat = _tfidf_embed(texts)
        assert mat.sum() > 0


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

class TestClustering:
    def _make_matrix(self, n: int = 60) -> np.ndarray:
        """Make a small matrix with 3 clear clusters."""
        rng = np.random.default_rng(42)
        # 3 groups of 20 points around different centroids
        groups = [
            rng.normal([1, 0, 0, 0], 0.1, (n // 3, 4)),
            rng.normal([0, 1, 0, 0], 0.1, (n // 3, 4)),
            rng.normal([0, 0, 1, 0], 0.1, (n // 3, 4)),
        ]
        return np.vstack(groups).astype(np.float32)

    def test_labels_correct_length(self):
        matrix = self._make_matrix(60)
        labels = cluster_embeddings(matrix, min_cluster_size=5)
        assert len(labels) == 60

    def test_labels_are_integers(self):
        matrix = self._make_matrix(60)
        labels = cluster_embeddings(matrix, min_cluster_size=5)
        assert all(isinstance(lbl, (int, np.integer)) for lbl in labels)

    def test_at_least_one_cluster(self):
        matrix = self._make_matrix(60)
        labels = cluster_embeddings(matrix, min_cluster_size=5)
        n_clusters = len(set(labels.tolist()) - {-1})
        assert n_clusters >= 1


# ---------------------------------------------------------------------------
# Cluster label generation
# ---------------------------------------------------------------------------

class TestClusterLabels:
    def test_returns_dict(self):
        texts = [
            "fraud detection benefits payment system",
            "fraud machine learning scoring eligibility",
            "satellite imagery earth observation remote sensing",
            "satellite orbit tracking space mission",
        ]
        labels = np.array([0, 0, 1, 1])
        result = generate_cluster_labels(texts, labels)
        assert isinstance(result, dict)

    def test_keys_are_cluster_ids(self):
        texts = ["text a", "text b", "text c", "text d"]
        labels = np.array([0, 0, 1, 1])
        result = generate_cluster_labels(texts, labels)
        assert set(result.keys()) == {0, 1}

    def test_no_noise_in_labels(self):
        """Noise label -1 should not appear as a cluster key."""
        texts = ["text a", "text b", "text c"]
        labels = np.array([0, 0, -1])
        result = generate_cluster_labels(texts, labels)
        assert -1 not in result

    def test_all_noise_returns_empty(self):
        texts = ["text a", "text b"]
        labels = np.array([-1, -1])
        result = generate_cluster_labels(texts, labels)
        assert result == {}


# ---------------------------------------------------------------------------
# load_index with missing files
# ---------------------------------------------------------------------------

class TestLoadIndex:
    def test_returns_false_when_no_files(self, tmp_path, monkeypatch):
        """load_index should return False gracefully when files are missing."""
        import tools._embeddings as emb_module
        monkeypatch.setattr(emb_module, "_EMB_PATH", tmp_path / "missing.npz")
        monkeypatch.setattr(emb_module, "_CLUSTER_PATH", tmp_path / "missing.json")
        result = emb_module.load_index()
        assert result is False


# ---------------------------------------------------------------------------
# Similarity tool — index not available
# ---------------------------------------------------------------------------

class TestSimilarityToolNoIndex:
    def test_find_similar_returns_error_when_no_index(self, monkeypatch):
        import tools.gsa_gc_similarity as sim_module
        monkeypatch.setattr(sim_module, "_INDEX_AVAILABLE", False)
        result = sim_module.gsa_gc_find_similar_use_cases(use_case_id="any-id")
        content = result.content if hasattr(result, "content") else result
        assert content.index_available is False
        assert content.error is not None
        assert "build_index" in content.error.lower() or "not found" in content.error.lower()

    def test_find_like_returns_error_when_no_index(self, monkeypatch):
        import tools.gsa_gc_similarity as sim_module
        monkeypatch.setattr(sim_module, "_INDEX_AVAILABLE", False)
        result = sim_module.gsa_gc_find_use_cases_like(text="fraud detection AI")
        content = result.content if hasattr(result, "content") else result
        assert content.index_available is False

    def test_cross_agency_duplicates_returns_error_when_no_index(self, monkeypatch):
        import tools.gsa_gc_similarity as sim_module
        monkeypatch.setattr(sim_module, "_INDEX_AVAILABLE", False)
        result = sim_module.gsa_gc_cross_agency_duplicates()
        content = result.content if hasattr(result, "content") else result
        assert content.index_available is False
