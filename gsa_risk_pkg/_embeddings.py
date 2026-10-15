"""
Embedding and clustering engine for the Federal AI Use Case similarity tools.

Strategy
--------
* Text repr = use_case_name + topic_area + purpose_and_benefits + system_outputs
* Embeddings:
    - If WATSONX_API_KEY + WATSONX_URL + WATSONX_PROJECT_ID are set → watsonx.ai
    - Otherwise → scikit-learn TF-IDF (default for tests and local runs)
* Similarity → numpy cosine (no external vector DB)
* Clustering  → HDBSCAN (falls back to KMeans if unavailable)
* Cluster labels → top-TF-IDF terms per cluster (no LLM)

Precomputed index stored in:
    data/embeddings.npz   – "matrix" (N×D float32), "ids" (N,) str array
    data/clusters.json    – list of cluster dicts

Load at server startup via load_index(); used by all similarity tools.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_EMB_PATH = _DATA_DIR / "embeddings.npz"
_CLUSTER_PATH = _DATA_DIR / "clusters.json"

# ---------------------------------------------------------------------------
# Module-level index cache
# ---------------------------------------------------------------------------

_INDEX: dict = {}   # keys: "matrix", "ids"
_CLUSTERS: list = []


# ---------------------------------------------------------------------------
# Text representation
# ---------------------------------------------------------------------------

def build_text(rec) -> str:  # rec: NormalizedRecord
    """Build a single text string from the most informative fields."""
    parts = [
        rec.use_case_name or "",
        rec.topic_area or "",
        rec.purpose_and_benefits or "",
        rec.system_outputs or "",
    ]
    return " ".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# TF-IDF backend (always available)
# ---------------------------------------------------------------------------

def _tfidf_embed(texts: list[str]) -> np.ndarray:
    from sklearn.feature_extraction.text import TfidfVectorizer
    min_df = 2 if len(texts) >= 10 else 1
    vec = TfidfVectorizer(
        max_features=4096,
        ngram_range=(1, 2),
        min_df=min_df,
        sublinear_tf=True,
        strip_accents="unicode",
        analyzer="word",
    )
    mat = vec.fit_transform(texts)
    # Store vectorizer for free-text query encoding later
    _INDEX["_tfidf_vectorizer"] = vec
    return mat.toarray().astype(np.float32)


# ---------------------------------------------------------------------------
# sentence-transformers backend (local, no credentials needed)
# ---------------------------------------------------------------------------

# Cached model instance so we don't reload on every embed_query call
_ST_MODEL: object = None


def _st_embed(texts: list[str], model_name: str = "all-mpnet-base-v2") -> np.ndarray:
    """Embed texts with a local sentence-transformers model (CPU).

    Downloads the model on first call (~420 MB for all-mpnet-base-v2).
    Subsequent calls reuse the cached model.
    """
    global _ST_MODEL
    from sentence_transformers import SentenceTransformer

    if _ST_MODEL is None or getattr(_ST_MODEL, "_model_name", "") != model_name:
        print(f"  Loading sentence-transformers model '{model_name}'...")
        _ST_MODEL = SentenceTransformer(model_name)
        _ST_MODEL._model_name = model_name  # stash for cache check

    batch_size = 64
    n = len(texts)
    vecs = []
    for i in range(0, n, batch_size):
        batch_num = i // batch_size + 1
        n_batches = (n + batch_size - 1) // batch_size
        if batch_num == 1 or batch_num % 10 == 0 or batch_num == n_batches:
            print(f"  Encoding batch {batch_num}/{n_batches} "
                  f"({min(i + batch_size, n)}/{n} texts)...")
        vecs.append(_ST_MODEL.encode(texts[i : i + batch_size],
                                     show_progress_bar=False,
                                     convert_to_numpy=True))
    return np.vstack(vecs).astype(np.float32)


# ---------------------------------------------------------------------------
# watsonx.ai backend (optional, requires IBM Cloud credentials)
# ---------------------------------------------------------------------------

# Module-level IAM token cache  {token, expires_at}
_IAM_CACHE: dict = {}


def _get_iam_token(api_key: str) -> str:
    """Return a valid IAM bearer token, refreshing if expired (with 60s buffer)."""
    import time
    import requests as _req

    now = time.time()
    if _IAM_CACHE.get("token") and _IAM_CACHE.get("expires_at", 0) > now + 60:
        return _IAM_CACHE["token"]

    resp = _req.post(
        "https://iam.cloud.ibm.com/identity/token",
        data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    _IAM_CACHE["token"] = data["access_token"]
    _IAM_CACHE["expires_at"] = now + data.get("expires_in", 3600)
    return _IAM_CACHE["token"]


def _wxai_embed(texts: list[str], model_id: str = "ibm/slate-125m-english-rtrvr-v2") -> np.ndarray:
    """Embed texts via watsonx.ai Embeddings API.

    Requires env vars: WATSONX_API_KEY, WATSONX_URL, WATSONX_PROJECT_ID.
    Batches of 32 to stay within API limits. Reuses IAM token across batches.
    """
    import requests as _req

    api_key = os.environ["WATSONX_API_KEY"]
    base_url = os.environ["WATSONX_URL"].rstrip("/")
    project_id = os.environ["WATSONX_PROJECT_ID"]

    batch_size = 32
    n_batches = (len(texts) + batch_size - 1) // batch_size
    all_vecs: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch_num = i // batch_size + 1
        if batch_num == 1 or batch_num % 10 == 0 or batch_num == n_batches:
            print(f"  Embedding batch {batch_num}/{n_batches} "
                  f"({min(i + batch_size, len(texts))}/{len(texts)} texts)...")

        token = _get_iam_token(api_key)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        batch = texts[i : i + batch_size]
        payload = {
            "model_id": model_id,
            "inputs": batch,
            "project_id": project_id,
        }
        resp = _req.post(
            f"{base_url}/ml/v1/text/embeddings?version=2024-03-14",
            json=payload,
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        for result in resp.json()["results"]:
            all_vecs.append(result["embedding"])

    return np.array(all_vecs, dtype=np.float32)


# ---------------------------------------------------------------------------
# Embed dispatcher
# ---------------------------------------------------------------------------

def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed texts.  Priority order:
    1. watsonx.ai  — if WATSONX_API_KEY + WATSONX_URL + WATSONX_PROJECT_ID are set
    2. sentence-transformers (local CPU) — if sentence_transformers is installed
    3. TF-IDF — always available, keyword-overlap only (lowest quality)
    """
    use_wxai = all(
        os.environ.get(k) for k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID")
    )
    if use_wxai:
        model_id = os.environ.get(
            "WATSONX_EMBEDDING_MODEL", "ibm/slate-125m-english-rtrvr-v2"
        )
        print(f"  Backend: watsonx.ai  model={model_id}")
        _INDEX["_backend"] = "wxai"
        _INDEX["_model_id"] = model_id
        return _wxai_embed(texts, model_id=model_id)

    try:
        import sentence_transformers  # noqa: F401
        model_name = os.environ.get("ST_EMBEDDING_MODEL", "all-mpnet-base-v2")
        print(f"  Backend: sentence-transformers  model={model_name}")
        _INDEX["_backend"] = "st"
        _INDEX["_model_id"] = model_name
        return _st_embed(texts, model_name=model_name)
    except ImportError:
        pass

    print("  Backend: TF-IDF (install sentence-transformers for dense embeddings)")
    _INDEX["_backend"] = "tfidf"
    _INDEX["_model_id"] = "tfidf"
    return _tfidf_embed(texts)


# ---------------------------------------------------------------------------
# Single query embed (for free-text search)
# ---------------------------------------------------------------------------

def embed_query(query: str) -> np.ndarray:
    """Embed a single query string against the loaded index.

    Automatically uses the same backend (TF-IDF or watsonx.ai) the index was
    built with, so query vectors are always in the same space as the index.
    """
    if "matrix" not in _INDEX:
        raise RuntimeError("Index not loaded. Call load_index() first.")

    backend = _INDEX.get("_backend", "tfidf")

    if backend == "wxai":
        model_id = _INDEX.get("_model_id", "ibm/slate-125m-english-rtrvr-v2")
        mat = _wxai_embed([query], model_id=model_id)
        return mat[0]

    if backend == "st":
        model_name = _INDEX.get("_model_id", "all-mpnet-base-v2")
        mat = _st_embed([query], model_name=model_name)
        return mat[0]

    # TF-IDF path — use the stored vectorizer for in-space transformation
    vec = _INDEX.get("_tfidf_vectorizer")
    if vec is not None:
        arr = vec.transform([query]).toarray().astype(np.float32)
        return arr[0]

    raise RuntimeError(
        "TF-IDF vectorizer not found in index. Rebuild the index with "
        "`python scripts/build_index.py --force`."
    )


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

def cosine_similarity_matrix(matrix: np.ndarray) -> np.ndarray:
    """Return NxN cosine similarity matrix."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-10, norms)
    normed = matrix / norms
    return normed @ normed.T


def cosine_similarity_single(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Return 1D array of cosine similarities between query_vec and each row in matrix."""
    q_norm = np.linalg.norm(query_vec)
    if q_norm == 0:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    q = query_vec / q_norm
    row_norms = np.linalg.norm(matrix, axis=1)
    row_norms = np.where(row_norms == 0, 1e-10, row_norms)
    return (matrix @ q) / row_norms


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def cluster_embeddings(
    matrix: np.ndarray,
    min_cluster_size: int = 10,
    n_clusters_fallback: int = 50,
) -> np.ndarray:
    """Cluster embeddings. Returns label array (len = N). Label -1 = noise (HDBSCAN)."""
    try:
        import hdbscan
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=5,
            metric="euclidean",
            core_dist_n_jobs=1,
        )
        labels = clusterer.fit_predict(matrix)
        n_clusters = len(set(labels) - {-1})
        print(f"  HDBSCAN: {n_clusters} clusters, {(labels == -1).sum()} noise points")
        return labels
    except Exception as e:
        print(f"  HDBSCAN failed ({e}), falling back to KMeans")
        from sklearn.cluster import KMeans
        km = KMeans(n_clusters=n_clusters_fallback, random_state=42, n_init="auto")
        labels = km.fit_predict(matrix)
        print(f"  KMeans: {n_clusters_fallback} clusters")
        return labels


# ---------------------------------------------------------------------------
# Cluster label generation (top TF-IDF terms per cluster)
# ---------------------------------------------------------------------------

def generate_cluster_labels(
    texts: list[str],
    labels: np.ndarray,
    top_n: int = 5,
) -> dict[int, str]:
    """Return {cluster_id: label_string} using top TF-IDF terms per cluster."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    unique_labels = sorted(set(labels.tolist()) - {-1})
    cluster_texts: dict[int, list[str]] = {lbl: [] for lbl in unique_labels}
    for text, lbl in zip(texts, labels.tolist()):
        if lbl != -1:
            cluster_texts[lbl].append(text)

    if not cluster_texts:
        return {}

    # Fit on all non-noise texts
    all_texts = [" ".join(cluster_texts[lbl]) for lbl in unique_labels]
    vec = TfidfVectorizer(max_features=2000, ngram_range=(1, 2), stop_words="english")
    mat = vec.fit_transform(all_texts)
    feature_names = vec.get_feature_names_out()

    labels_dict: dict[int, str] = {}
    for i, lbl in enumerate(unique_labels):
        row = mat[i].toarray()[0]
        top_indices = row.argsort()[::-1][:top_n]
        top_terms = [feature_names[j] for j in top_indices if row[j] > 0]
        labels_dict[lbl] = " | ".join(top_terms) if top_terms else f"cluster_{lbl}"

    return labels_dict


# ---------------------------------------------------------------------------
# Build index (called from scripts/build_index.py)
# ---------------------------------------------------------------------------

def build_and_save_index(records) -> None:
    """Embed all records, cluster, save to data/. Called from build_index.py."""
    _DATA_DIR.mkdir(exist_ok=True)

    print(f"Building text representations for {len(records)} records...")
    texts = [build_text(r) for r in records]
    ids = np.array([r.record_id for r in records], dtype=object)

    print("Computing embeddings...")
    matrix = embed_texts(texts)
    print(f"  Embedding shape: {matrix.shape}")

    print("Saving embeddings.npz...")
    backend = _INDEX.get("_backend", "tfidf")
    model_id = _INDEX.get("_model_id", "tfidf")
    np.savez_compressed(
        str(_EMB_PATH),
        matrix=matrix,
        ids=ids,
        backend=np.array([backend], dtype=object),
        model_id=np.array([model_id], dtype=object),
    )

    print("Clustering...")
    labels = cluster_embeddings(matrix)

    print("Generating cluster labels...")
    label_map = generate_cluster_labels(texts, labels)

    # Build cluster metadata
    from collections import defaultdict
    cluster_members: dict[int, list[int]] = defaultdict(list)
    for idx, lbl in enumerate(labels.tolist()):
        cluster_members[lbl].append(idx)

    clusters = []
    for lbl in sorted(cluster_members.keys()):
        if lbl == -1:
            continue
        member_indices = cluster_members[lbl]
        member_recs = [records[i] for i in member_indices]
        agencies = sorted({r.agency for r in member_recs})
        sample_ids = [r.record_id for r in member_recs[:5]]
        clusters.append({
            "cluster_id": str(lbl),
            "label": label_map.get(lbl, f"cluster_{lbl}"),
            "size": len(member_indices),
            "agencies": agencies,
            "agency_count": len(agencies),
            "member_indices": member_indices,
            "sample_record_ids": sample_ids,
        })

    # Noise cluster
    noise_indices = cluster_members.get(-1, [])
    if noise_indices:
        clusters.append({
            "cluster_id": "-1",
            "label": "noise / unclustered",
            "size": len(noise_indices),
            "agencies": [],
            "agency_count": 0,
            "member_indices": noise_indices,
            "sample_record_ids": [],
        })

    print(f"Saving clusters.json ({len(clusters)} clusters)...")
    _CLUSTER_PATH.write_text(json.dumps(clusters, indent=2))
    print("Done.")


# ---------------------------------------------------------------------------
# Load index (called at server startup)
# ---------------------------------------------------------------------------

def load_index() -> bool:
    """Load precomputed embeddings and clusters into module-level cache.

    Returns True if loaded successfully, False if files not found.
    """
    global _CLUSTERS
    if not _EMB_PATH.exists() or not _CLUSTER_PATH.exists():
        return False

    data = np.load(str(_EMB_PATH), allow_pickle=True)
    _INDEX["matrix"] = data["matrix"].astype(np.float32)
    _INDEX["ids"] = list(data["ids"])
    _INDEX["id_to_idx"] = {rid: i for i, rid in enumerate(_INDEX["ids"])}
    # Restore backend metadata so embed_query knows which path to take
    if "backend" in data:
        _INDEX["_backend"] = str(data["backend"][0])
    if "model_id" in data:
        _INDEX["_model_id"] = str(data["model_id"][0])

    _CLUSTERS = json.loads(_CLUSTER_PATH.read_text())
    return True


def index_loaded() -> bool:
    return "matrix" in _INDEX


def get_index() -> dict:
    return _INDEX


def get_clusters() -> list:
    return _CLUSTERS
