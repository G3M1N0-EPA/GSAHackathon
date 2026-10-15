#!/usr/bin/env python3
"""
Build the embedding index and cluster assignments.

Usage:
    python scripts/build_index.py [--force]

Reads:  OMB inventory CSVs (live from GitHub, or cached by _data_loader)
Writes: data/embeddings.npz   – embedding matrix + record ID array
        data/clusters.json    – cluster metadata with labels and member IDs

Environment variables (optional, watsonx.ai embeddings):
    WATSONX_API_KEY          IBM Cloud API key
    WATSONX_URL              watsonx.ai service URL
    WATSONX_PROJECT_ID       Project ID
    WATSONX_EMBEDDING_MODEL  Model ID (default: ibm/slate-125m-english-rtrvr)

If these are not set, scikit-learn TF-IDF is used instead (no credentials needed).

Re-run this script any time the inventory is updated or weights are changed.
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools._data_loader import load_records
from tools._embeddings import _CLUSTER_PATH, _EMB_PATH, build_and_save_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build embedding index for Federal AI use cases")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even if data/embeddings.npz already exists",
    )
    args = parser.parse_args()

    if not args.force and _EMB_PATH.exists() and _CLUSTER_PATH.exists():
        print(f"Index already exists at {_EMB_PATH}")
        print("Use --force to rebuild.")
        sys.exit(0)

    print("Loading records from OMB inventories...")
    records = load_records(force_reload=True)
    print(f"Loaded {len(records)} records.")

    build_and_save_index(records)
    print(f"\nIndex saved to {_EMB_PATH.parent}/")


if __name__ == "__main__":
    main()
