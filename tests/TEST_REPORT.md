# TEST REPORT — GSA Federal AI Risk Analyst

**Date:** 2026-10-09  
**Environment:** `gsa-hackathon` (IBM watsonx Orchestrate, ca-tor)  
**Agent:** `gsa_gc_federal_ai_agent`  
**Status:** ✅ deployed and tested (2/2)

---

## Unit Tests

| Suite | Count | Result |
|---|---|---|
| `test_risk_scoring.py` | 19 | ✅ all pass |
| `test_similarity.py` | 22 | ✅ all pass |
| `test_external_evidence.py` | 15 | ✅ all pass |
| **Total** | **56** | **✅ 56/56 pass** |

```
$ PYTHONPATH=. python -m pytest tests/ -v
============================== 56 passed in 3.34s ==============================
```

## Lint

```
$ ruff check tools/ scripts/ tests/ config/ --select E,F,W --ignore E501
All checks passed!
```

Remaining notes:
- `E501` (line-too-long): all violations are in long CSV column names, docstrings, and rationale strings — unfixable without breaking functionality.
- `BLE001` (broad except): intentional defensive code for network/file I/O in tool functions.

---

## Smoke Tests

### Turn 1 — Elevated risk count + top agency
**Prompt:** "How many federal AI use cases are Elevated risk, and which agency has the most?"  
**Tool called:** `gsa_gc_top_risk_use_cases` (band=Elevated, limit=1000)  
**Response:** 161 Elevated-risk use cases. Department of Justice leads with 80 Elevated cases.  
**Ground truth:** 161 Elevated / DOJ = 80 ✅  
**Result:** PASS

### Turn 2 — Score a specific use case (multi-turn)
**Prompt:** "Score use case DHS-185 (Babel) and show the top contributing risk factors."  
**Tool called:** `gsa_gc_score_use_case` (use_case_id=DHS-185)  
**Response:** Score: 65 / Elevated — top 3 factors: High-impact designation (+25), Affects individuals' rights (+20), Development stage deployed (+10)  
**Ground truth:** DHS-185 score=65 ✅  
**Result:** PASS (multi-turn context used correctly — no re-introduction of agent)

---

## Known Issue Resolved During Testing

**Root cause:** `gsa_gc_risk_scoring.py`, `gsa_gc_similarity.py`, `gsa_gc_external_evidence.py` were
deployed as flat files (no `--package-root`). The bundled copies in `gsa_risk_pkg/` still referenced
`from gsa_risk_pkg._data_loader import ...` (wrong — package prefix must be stripped when
`--package-root gsa_risk_pkg` is used).

**Fix applied:**
1. `sed -i 's/from gsa_risk_pkg\./from /g'` applied to all 4 bundled files.
2. All 10 cross-import tools re-imported with `--package-root gsa_risk_pkg`.

**Going forward:** `scripts/import-all.sh` documents the correct import commands.

---

## How to Test Manually

**UI:** Open `gsa_gc_federal_ai_agent` in the wxO web UI. Use starter prompts.

**CLI:**
```bash
orchestrate chat ask -n gsa_gc_federal_ai_agent "How many Elevated-risk AI use cases are there?" -r
```
⚠️ Can hang on SaaS — prefer the UI or Bob's `chat_with_agent`.

**Sample prompts and expected outputs:**

| Prompt | Expected |
|---|---|
| "How many federal AI use cases are Elevated risk?" | 161 Elevated (DoJ leads with 80) |
| "Score use case DHS-185 and explain the top factors" | Score 65 / Elevated; factors: HI designation, rights impact, deployed stage |
| "Find cross-agency near-duplicates at threshold 0.95 — top 5 pairs" | Pairs with similarity ≥0.95 from different agencies |
| "Show the largest clusters of similar use cases" | List of 31 clusters with top keywords and agency counts |
| "Search CourtListener for facial recognition AI cases" | Warning if no token set, else unconfirmed CourtListener leads |
