#!/usr/bin/env bash
# import-all.sh — re-imports all GSA Hackathon tools + agent into the active wxO env.
# Run from the project root after activating the venv:
#   source venv/bin/activate
#   orchestrate env activate gsa-hackathon --api-key "$IBM_CLOUD_API_KEY"
#   ./scripts/import-all.sh
set -e

# Standalone tool (no cross-imports — no --package-root needed)
orchestrate tools import -k python -f tools/gsa_gc_federal_ai_usecase_aggregator.py

# Bundled tools (cross-import _data_loader / _scorer / _embeddings)
# Must use --package-root gsa_risk_pkg so the platform bundles the helpers.
# -r requirements.txt installs sentence-transformers (dense embeddings) at runtime.
orchestrate tools import -k python \
  -f gsa_risk_pkg/gsa_gc_risk_scoring.py \
  --package-root gsa_risk_pkg \
  -r requirements.txt

orchestrate tools import -k python \
  -f gsa_risk_pkg/gsa_gc_similarity.py \
  --package-root gsa_risk_pkg \
  -r requirements.txt

orchestrate tools import -k python \
  -f gsa_risk_pkg/gsa_gc_external_evidence.py \
  --package-root gsa_risk_pkg \
  -r requirements.txt

# Agent
orchestrate agents import -f agents/gsa_gc_federal_ai_agent.yaml

echo "All tools and agent imported successfully."
