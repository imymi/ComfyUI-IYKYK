#!/usr/bin/env python3
"""
[DEPRECATED] scripts/build_full_m1_selector_ledger.py

This script has been deprecated in favor of `scripts/generate_m1_ssot.py`.
`scripts/generate_m1_ssot.py` serves as the authoritative Single Source of Truth (SSoT)
for generating:
- docs/m1_selector_attribution_ledger.tsv
- docs/m1_196_cell_affinity_audit.md
- docs/m1_selector_attribution_summary.md

Do NOT run or maintain this legacy script.
"""

import sys

def main():
    print(
        "ERROR: scripts/build_full_m1_selector_ledger.py is DEPRECATED.\n"
        "Please use `python3 scripts/generate_m1_ssot.py` as the Single Source of Truth.",
        file=sys.stderr,
    )
    sys.exit(1)

if __name__ == "__main__":
    main()
