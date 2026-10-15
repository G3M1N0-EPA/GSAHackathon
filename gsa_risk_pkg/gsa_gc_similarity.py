"""
GSA Hackathon: Federal AI Use Case Similarity and Clustering Tools

Five tools:
  - gsa_gc_find_similar_use_cases   -- k-NN by record ID
  - gsa_gc_find_use_cases_like      -- k-NN by free-text description
  - gsa_gc_list_clusters            -- paginated cluster listing
  - gsa_gc_cluster_details          -- full cluster detail
  - gsa_gc_cross_agency_duplicates  -- near-duplicate pairs from different agencies
"""

import json
from pathlib import Path
from typing import Any, List, Optional

import numpy as np
from pydantic import BaseModel

from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission
from _data_loader import load_records, get_record_by_id
from _scorer import score_record, score_all
from _embeddings import (
    load_index,
    index_loaded,
    get_index,
    get_clusters,
    cosine_similarity_single,
    embed_query,
)

# ---------------------------------------------------------------------------
# Bootstrap: load index once at module import time
# ---------------------------------------------------------------------------

_INDEX_AVAILABLE = load_index()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_KNOWN_ISSUES_PATH = _DATA_DIR / "known_issues.json"


def _load_known_issues() -> dict:
    if not _KNOWN_ISSUES_PATH.exists():
        return {}
    try:
        entries = json.loads(_KNOWN_ISSUES_PATH.read_text())
        result: dict = {}
        for e in entries:
            rid = e.get("use_case_id", "")
            if rid:
                result.setdefault(rid, []).append(e)
        return result
    except Exception:
        return {}


def _index_not_loaded_msg() -> str:
    return (
        "Embedding index not found. Run `python scripts/build_index.py` first "
        "to generate data/embeddings.npz and data/clusters.json."
    )


def _get_risk_band(record_id: str, ev_map: dict) -> str:
    rec = get_record_by_id(record_id)
    if rec is None:
        return "Unknown"
    result = score_record(rec, ev_map.get(record_id))
    return result["band"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SimilarUseCase(BaseModel):
    record_id: str
    agency: str
    bureau: Optional[str] = None
    use_case_name: str
    inventory_year: str
    topic_area: Optional[str] = None
    similarity: float
    risk_band: str

    model_config = {"arbitrary_types_allowed": True}


class SimilarResult(BaseModel):
    query_record_id: str
    query_agency: str
    query_use_case_name: str
    results: List[Any]
    index_available: bool
    error: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


class FreeTextResult(BaseModel):
    query_text: str
    results: List[Any]
    index_available: bool
    error: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


class ClusterListResult(BaseModel):
    total_clusters: int
    offset: int
    limit: int
    clusters: List[Any]
    index_available: bool

    model_config = {"arbitrary_types_allowed": True}


class ClusterDetailResult(BaseModel):
    cluster_id: str
    label: str
    size: int
    agency_count: int
    agencies: List[str]
    use_cases: List[Any]
    index_available: bool
    error: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


class DuplicatePair(BaseModel):
    record_id_a: str
    agency_a: str
    use_case_name_a: str
    record_id_b: str
    agency_b: str
    use_case_name_b: str
    similarity: float
    risk_band_a: str
    risk_band_b: str

    model_config = {"arbitrary_types_allowed": True}


class DuplicatesResult(BaseModel):
    threshold: float
    total_pairs: int
    offset: int
    limit: int
    pairs: List[Any]
    index_available: bool
    note: str

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Tool 1: find_similar_use_cases
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_find_similar_use_cases(
    use_case_id: str,
    k: int = 10,
    exclude_same_agency: bool = False,
) -> SimilarResult:
    """Find federal AI use cases most similar to a given use case by its record ID.

    Uses precomputed embeddings (TF-IDF or watsonx.ai) and cosine similarity to
    find the k nearest neighbours. Each result includes the similarity score and
    the use case's risk band.

    Args:
        use_case_id (str): The record_id of the reference use case. Obtain IDs from gsa_gc_top_risk_use_cases.
        k (int): Number of similar use cases to return. Defaults to 10.
        exclude_same_agency (bool): If True, exclude results from the same agency as the query. Defaults to False.
    Returns:
        SimilarResult: Ranked similar use cases with similarity scores and risk bands.
    """
    if not _INDEX_AVAILABLE:
        return SimilarResult(
            query_record_id=use_case_id, query_agency="", query_use_case_name="",
            results=[], index_available=False, error=_index_not_loaded_msg(),
        )

    idx_map = get_index()["id_to_idx"]
    matrix = get_index()["matrix"]
    ids = get_index()["ids"]

    if use_case_id not in idx_map:
        return SimilarResult(
            query_record_id=use_case_id, query_agency="", query_use_case_name="",
            results=[], index_available=True,
            error=f"record_id '{use_case_id}' not in embedding index.",
        )

    query_rec = get_record_by_id(use_case_id)
    ev_map = _load_known_issues()

    query_vec = matrix[idx_map[use_case_id]]
    sims = cosine_similarity_single(query_vec, matrix)

    # Sort descending, skip self
    order = np.argsort(sims)[::-1]
    results = []
    for i in order:
        rid = ids[i]
        if rid == use_case_id:
            continue
        rec = get_record_by_id(rid)
        if rec is None:
            continue
        if exclude_same_agency and query_rec and rec.agency == query_rec.agency:
            continue
        results.append({
            "record_id": rid,
            "agency": rec.agency,
            "bureau": rec.bureau,
            "use_case_name": rec.use_case_name,
            "inventory_year": rec.inventory_year,
            "topic_area": rec.topic_area,
            "similarity": round(float(sims[i]), 4),
            "risk_band": _get_risk_band(rid, ev_map),
        })
        if len(results) >= k:
            break

    return SimilarResult(
        query_record_id=use_case_id,
        query_agency=query_rec.agency if query_rec else "",
        query_use_case_name=query_rec.use_case_name if query_rec else "",
        results=results,
        index_available=True,
    )


# ---------------------------------------------------------------------------
# Tool 2: find_use_cases_like
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_find_use_cases_like(
    text: str,
    k: int = 10,
) -> FreeTextResult:
    """Find federal AI use cases that match a free-text description.

    Embeds the query text and returns the k most similar use cases by cosine
    similarity. Use this when you have a description of a capability or risk
    scenario and want to find matching use cases in the inventory.

    Args:
        text (str): Free-text description of the AI capability or scenario to search for.
        k (int): Number of results to return. Defaults to 10.
    Returns:
        FreeTextResult: Ranked matching use cases with similarity scores and risk bands.
    """
    if not _INDEX_AVAILABLE:
        return FreeTextResult(
            query_text=text, results=[], index_available=False,
            error=_index_not_loaded_msg(),
        )

    try:
        query_vec = embed_query(text)
    except Exception as exc:
        return FreeTextResult(
            query_text=text, results=[], index_available=True,
            error=f"Embedding failed: {exc}",
        )

    matrix = get_index()["matrix"]
    ids = get_index()["ids"]
    ev_map = _load_known_issues()

    sims = cosine_similarity_single(query_vec, matrix)
    order = np.argsort(sims)[::-1]

    results = []
    for i in order:
        rid = ids[i]
        rec = get_record_by_id(rid)
        if rec is None:
            continue
        results.append({
            "record_id": rid,
            "agency": rec.agency,
            "bureau": rec.bureau,
            "use_case_name": rec.use_case_name,
            "inventory_year": rec.inventory_year,
            "topic_area": rec.topic_area,
            "similarity": round(float(sims[i]), 4),
            "risk_band": _get_risk_band(rid, ev_map),
        })
        if len(results) >= k:
            break

    return FreeTextResult(query_text=text, results=results, index_available=True)


# ---------------------------------------------------------------------------
# Tool 3: list_clusters
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_list_clusters(
    limit: int = 50,
    offset: int = 0,
) -> ClusterListResult:
    """List all clusters of similar federal AI use cases, sorted by size.

    Each cluster is a group of semantically similar use cases identified by
    automated clustering. The label is generated from the top TF-IDF terms
    in the cluster. Clusters spanning multiple agencies highlight shared
    capabilities or potential duplication.

    Args:
        limit (int): Maximum number of clusters to return. Defaults to 50.
        offset (int): Pagination offset. Defaults to 0.
    Returns:
        ClusterListResult: Paginated list of clusters with labels, sizes, and agencies.
    """
    clusters = get_clusters()
    real_clusters = [c for c in clusters if c["cluster_id"] != "-1"]
    real_clusters.sort(key=lambda c: c["size"], reverse=True)
    page = real_clusters[offset: offset + limit]

    compact = [
        {
            "cluster_id": c["cluster_id"],
            "label": c["label"],
            "size": c["size"],
            "agency_count": c["agency_count"],
            "top_agencies": c["agencies"][:5],
            "sample_record_ids": c["sample_record_ids"],
        }
        for c in page
    ]

    return ClusterListResult(
        total_clusters=len(real_clusters),
        offset=offset,
        limit=limit,
        clusters=compact,
        index_available=_INDEX_AVAILABLE,
    )


# ---------------------------------------------------------------------------
# Tool 4: cluster_details
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_cluster_details(cluster_id: str) -> ClusterDetailResult:
    """Return full details of a specific cluster including all member use cases.

    Shows every use case in the cluster with its agency, name, topic area,
    development stage, and risk band. Use this to inspect a cluster after
    finding it via gsa_gc_list_clusters.

    Args:
        cluster_id (str): The cluster_id from gsa_gc_list_clusters output.
    Returns:
        ClusterDetailResult: Full cluster membership with use case details and risk bands.
    """
    if not _INDEX_AVAILABLE:
        return ClusterDetailResult(
            cluster_id=cluster_id, label="", size=0, agency_count=0,
            agencies=[], use_cases=[], index_available=False,
            error=_index_not_loaded_msg(),
        )

    clusters = get_clusters()
    cluster = next((c for c in clusters if c["cluster_id"] == cluster_id), None)
    if cluster is None:
        return ClusterDetailResult(
            cluster_id=cluster_id, label="", size=0, agency_count=0,
            agencies=[], use_cases=[], index_available=True,
            error=f"Cluster '{cluster_id}' not found.",
        )

    ids = get_index()["ids"]
    ev_map = _load_known_issues()
    use_cases = []
    for idx in cluster["member_indices"]:
        if idx >= len(ids):
            continue
        rid = ids[idx]
        rec = get_record_by_id(rid)
        if rec is None:
            continue
        use_cases.append({
            "record_id": rid,
            "agency": rec.agency,
            "bureau": rec.bureau,
            "use_case_name": rec.use_case_name,
            "topic_area": rec.topic_area,
            "development_stage": rec.development_stage,
            "inventory_year": rec.inventory_year,
            "risk_band": _get_risk_band(rid, ev_map),
        })

    return ClusterDetailResult(
        cluster_id=cluster_id,
        label=cluster["label"],
        size=cluster["size"],
        agency_count=cluster["agency_count"],
        agencies=cluster["agencies"],
        use_cases=use_cases,
        index_available=True,
    )


# ---------------------------------------------------------------------------
# Tool 5: cross_agency_duplicates
# ---------------------------------------------------------------------------

@tool(permission=ToolPermission.READ_ONLY)
def gsa_gc_cross_agency_duplicates(
    threshold: float = 0.85,
    limit: int = 50,
    offset: int = 0,
) -> DuplicatesResult:
    """Find near-duplicate AI use cases from different federal agencies.

    Identifies pairs of use cases above the similarity threshold that come from
    different agencies. These are candidates for shared-service consolidation,
    joint procurement, or contract duplication review.

    Args:
        threshold (float): Minimum cosine similarity to consider a duplicate. Defaults to 0.85.
        limit (int): Maximum number of pairs to return. Defaults to 50.
        offset (int): Pagination offset. Defaults to 0.
    Returns:
        DuplicatesResult: Paginated list of near-duplicate cross-agency pairs with similarity scores and risk bands.
    """
    if not _INDEX_AVAILABLE:
        return DuplicatesResult(
            threshold=threshold, total_pairs=0, offset=offset, limit=limit,
            pairs=[], index_available=False,
            note=_index_not_loaded_msg(),
        )

    matrix = get_index()["matrix"]
    ids = get_index()["ids"]
    ev_map = _load_known_issues()

    # Build agency lookup
    id_to_agency: dict = {}
    id_to_name: dict = {}
    all_records = load_records()
    for r in all_records:
        id_to_agency[r.record_id] = r.agency
        id_to_name[r.record_id] = r.use_case_name

    # Compute pairwise similarities block-by-block to avoid OOM on large N
    n = matrix.shape[0]
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-10, norms)
    normed = (matrix / norms).astype(np.float32)

    pairs: list[dict] = []
    block = 256  # rows per block
    for i_start in range(0, n, block):
        i_end = min(i_start + block, n)
        block_sims = normed[i_start:i_end] @ normed.T  # (block, N)
        rows, cols = np.where(block_sims >= threshold)
        for r, c in zip(rows.tolist(), cols.tolist()):
            global_i = i_start + r
            global_j = c
            if global_j <= global_i:
                continue  # upper-triangle only, skip self
            rid_a = ids[global_i]
            rid_b = ids[global_j]
            agency_a = id_to_agency.get(rid_a, "")
            agency_b = id_to_agency.get(rid_b, "")
            if agency_a == agency_b:
                continue  # same agency
            pairs.append({
                "record_id_a": rid_a,
                "agency_a": agency_a,
                "use_case_name_a": id_to_name.get(rid_a, ""),
                "record_id_b": rid_b,
                "agency_b": agency_b,
                "use_case_name_b": id_to_name.get(rid_b, ""),
                "similarity": round(float(block_sims[r, global_j]), 4),
                "risk_band_a": _get_risk_band(rid_a, ev_map),
                "risk_band_b": _get_risk_band(rid_b, ev_map),
            })
        if len(pairs) > limit + offset + 5000:
            break  # safety: stop early if we have plenty

    # Sort by similarity descending
    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    total = len(pairs)
    page = pairs[offset: offset + limit]

    return DuplicatesResult(
        threshold=threshold,
        total_pairs=total,
        offset=offset,
        limit=limit,
        pairs=page,
        index_available=True,
        note=(
            "Pairs sorted by similarity descending. "
            "High similarity across agencies may indicate shared-service candidates, "
            "common COTS products, or contract consolidation opportunities."
        ),
    )
