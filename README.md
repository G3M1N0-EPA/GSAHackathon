# Federal AI Use Case Risk Analyst

A watsonx Orchestrate agent and tool suite that collects, scores, clusters,
and analyses USA federal government AI use cases from the official OMB 2024
and 2025 inventories.

---

## What it does

| Capability | Tools |
|---|---|
| **Inventory aggregation** | Fetches and deduplicates 6,498 use cases from 3 OMB CSVs |
| **Litigation-risk scoring** | Deterministic 0–100 score with per-factor breakdown |
| **Similarity search** | k-NN by record ID or free-text description |
| **Clustering** | HDBSCAN/KMeans clusters with TF-IDF labels |
| **Cross-agency duplicates** | Near-duplicate pairs — procurement consolidation signals |
| **Litigation search** | CourtListener REST API (unconfirmed leads) |
| **Risk propagation** | Propagates known issues to similar use cases at other agencies |

---

## Quick start

```bash
# 1. Activate the environment
source venv/bin/activate

# 2. Activate the wxO environment
orchestrate env activate gsa-hackathon --api-key "$IBM_CLOUD_API_KEY"

# 3. Build the embedding index (required for similarity tools)
python scripts/build_index.py

# 4. Run all tests
PYTHONPATH=. python -m pytest tests/ -v

# 5. Chat with the agent
orchestrate chat ask -n gsa_gc_federal_ai_agent \
  "Score use case DHS-185 and explain the top risk factors" -r
```

---

## Tools reference

### Phase 1 — Inventory

| Tool | Description |
|---|---|
| `gsa_gc_federal_ai_usecase_aggregator` | Fetch all OMB use cases, return summary with breakdowns |

### Phase 2 — Litigation-risk scoring

| Tool | Description |
|---|---|
| `gsa_gc_score_use_case(use_case_id)` | Score one use case by record ID |
| `gsa_gc_top_risk_use_cases(agency, topic, band, limit, offset)` | Ranked list with filters |
| `gsa_gc_explain_risk_rubric()` | Full rubric: factors, weights, bands, disclaimer |

### Phase 3 — Similarity and clustering

| Tool | Description |
|---|---|
| `gsa_gc_find_similar_use_cases(use_case_id, k, exclude_same_agency)` | k-NN by record ID |
| `gsa_gc_find_use_cases_like(text, k)` | k-NN by free-text description |
| `gsa_gc_list_clusters(limit, offset)` | Paginated cluster list |
| `gsa_gc_cluster_details(cluster_id)` | Full cluster membership |
| `gsa_gc_cross_agency_duplicates(threshold, limit, offset)` | Near-duplicate cross-agency pairs |

### Phase 4 — External evidence and risk propagation

| Tool | Description |
|---|---|
| `gsa_gc_search_litigation(query, limit)` | CourtListener search (unconfirmed leads) |
| `gsa_gc_risk_propagation(use_case_id, k, min_similarity)` | Propagate known issues to similar use cases |

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `IBM_CLOUD_API_KEY` | Yes | IBM Cloud API key for wxO authentication |
| `COURTLISTENER_API_TOKEN` | No | Token for CourtListener API. If absent, `gsa_gc_search_litigation` returns a helpful warning instead of failing. Register free at [courtlistener.com](https://www.courtlistener.com/sign-in/) |
| `WATSONX_API_KEY` | No | IBM Cloud API key for watsonx.ai embeddings |
| `WATSONX_URL` | No | watsonx.ai service URL (e.g. `https://us-south.ml.cloud.ibm.com`) |
| `WATSONX_PROJECT_ID` | No | watsonx.ai project ID |
| `WATSONX_EMBEDDING_MODEL` | No | watsonx.ai embedding model ID. Default: `ibm/slate-125m-english-rtrvr` |

If `WATSONX_API_KEY`, `WATSONX_URL`, and `WATSONX_PROJECT_ID` are all set,
`scripts/build_index.py` will use watsonx.ai embeddings. Otherwise it uses
scikit-learn TF-IDF (no credentials needed, works offline).

---

## Rebuilding the embedding index

Run this after any of the following:
- The OMB inventory CSVs are updated on GitHub
- You add significant new entries to `data/known_issues.json`
- You change the TF-IDF parameters in `tools/_embeddings.py`

```bash
python scripts/build_index.py          # skip if index already exists
python scripts/build_index.py --force  # always rebuild
```

Output files:
- `data/embeddings.npz` — float32 matrix (6498 × 4096) + record ID array
- `data/clusters.json` — cluster metadata with labels and member indices

After rebuilding, re-import all bundled tools (or simply run `./scripts/import-all.sh`):
```bash
orchestrate tools import -k python \
  -f gsa_risk_pkg/gsa_gc_risk_scoring.py --package-root gsa_risk_pkg
orchestrate tools import -k python \
  -f gsa_risk_pkg/gsa_gc_similarity.py --package-root gsa_risk_pkg
orchestrate tools import -k python \
  -f gsa_risk_pkg/gsa_gc_external_evidence.py --package-root gsa_risk_pkg
```

> **Important:** The bundled files in `gsa_risk_pkg/` must use bare imports
> (`from _data_loader import ...`) — **not** `from gsa_risk_pkg._data_loader import ...`.
> The `--package-root gsa_risk_pkg` flag sets `gsa_risk_pkg/` as the runtime CWD,
> so `gsa_risk_pkg` is not a discoverable package name at that point.

---

## Curating `data/known_issues.json`

This file links specific use cases to known litigation, GAO findings, or IG
reports. The `gsa_gc_risk_propagation` tool reads it to flag similar use
cases at other agencies.

### Schema

```json
{
  "use_case_id":  "record_id from inventory (e.g. 'DHS-185')",
  "type":         "litigation | gao | ig | other",
  "title":        "Short title of the finding or case",
  "url":          "Canonical URL (docket, GAO report, IG report)",
  "date":         "YYYY-MM-DD",
  "summary":      "One-sentence plain-language description",
  "confirmed":    true,
  "added_by":     "curator initials or name",
  "notes":        "free-text notes (optional)"
}
```

### Workflow

1. Run `gsa_gc_search_litigation` to find CourtListener candidates
2. **Manually verify** the case relates to the specific use case — name matches
   are often false positives
3. Find the correct `record_id` via `gsa_gc_top_risk_use_cases` or
   `gsa_gc_find_use_cases_like`
4. Add the entry to `data/known_issues.json` with `"confirmed": true`
5. **Never auto-populate** this file from search results

> ⚠️ CourtListener matches from `gsa_gc_search_litigation` must be confirmed
> by a human before being added. A name similarity does not prove legal relevance.

---

## Risk scoring rubric

Scores are additive points (0–100) across six deterministic factors.
Bands: **Low** (0–29) · **Moderate** (30–59) · **Elevated** (60–100).

| Factor | Max points | Condition |
|---|---|---|
| High-impact designation | 25 | HIGH_IMPACT=25, PRESUMED_NOT_HI=20, UNKNOWN=10, NOT_HI=0 |
| Affects individuals | 20 | Sensitive topic area or rights/safety flag |
| Development stage | 10 | Deployed=10, Pilot=5 |
| Uses PII | 10 | has_pii=Yes |
| Missing safeguards (capped) | 15 | No assessment/review/monitoring/appeal on HI or PNHI cases |
| Waiver from minimum practices | 10 | Agency requested extension |
| External evidence (Phase 4) | 15/10 | Litigation=15, GAO/IG=10 |

Weights are in [`config/scoring_weights.json`](config/scoring_weights.json) — edit to retune without touching code.

Every score includes the disclaimer:
> *"Indicator for triage only, based on agency self-reported data. Not legal advice."*

---

## Running tests

```bash
PYTHONPATH=. python -m pytest tests/ -v
# 56 tests: 19 Phase 2 + 22 Phase 3 + 15 Phase 4
```

---

## Project structure

```
.
├── agents/
│   └── gsa_gc_federal_ai_agent.yaml   # wxO agent definition
├── config/
│   └── scoring_weights.json           # tunable risk-scoring weights
├── data/
│   ├── embeddings.npz                 # precomputed TF-IDF embeddings (6498×4096)
│   ├── clusters.json                  # HDBSCAN cluster metadata
│   └── known_issues.json              # curated evidence (litigation, GAO, IG)
├── docs/
│   └── agent_instructions.md          # detailed agent instructions
├── evaluation.xml                     # 10-question gold evaluation set
├── gsa_risk_pkg/                      # bundled package for wxO import
│   ├── _data_loader.py
│   ├── _scorer.py
│   ├── _embeddings.py
│   ├── gsa_gc_risk_scoring.py
│   ├── gsa_gc_similarity.py
│   ├── gsa_gc_external_evidence.py
│   ├── embeddings.npz                 # bundled for wxO runtime
│   └── clusters.json
├── scripts/
│   └── build_index.py                 # rebuild embeddings + clusters
├── tests/
│   ├── test_risk_scoring.py           # 19 tests
│   ├── test_similarity.py             # 22 tests
│   └── test_external_evidence.py      # 15 tests
└── tools/
    ├── _data_loader.py                # shared in-memory cache
    ├── _scorer.py                     # deterministic scoring engine
    ├── _embeddings.py                 # TF-IDF / watsonx.ai + clustering
    ├── gsa_gc_federal_ai_usecase_aggregator.py
    ├── gsa_gc_risk_scoring.py
    ├── gsa_gc_similarity.py
    └── gsa_gc_external_evidence.py
```
