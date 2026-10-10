# M2 Wildcards Batch Divergence Audit Report

- **Baseline Commit**: `6da94cb` (M1 Verified Baseline: `aa7581bc2304f6f75530d95ab4e1b75e7e1101720b1dfaf14c2a1c328fcd1139`)
- **Current Target**: Working Tree (`f7d8524cffeab2ce9d0dc65f504f2bc5f458b99521a6b0469a590606ba3c59c0`)
- **Total Seeds**: 10,000
- **Identical Seeds**: 6,887 (68.87%)
- **Divergent Seeds**: 3,113 (31.13%)
- **Unexplained Seeds**: **0** (Gate required: == 0)
- **Evidence Archive**: `audit_m2_evidence.json.gz` (3,113 divergent records with full atoms, decisions, and atomic causal proofs)
- **Evidence Archive SHA-256**: `3f2de1d5db0c98ce247a92e267e833a110f746d6d5a129e71e696641efa40634`
- **Source Atoms Digest**: `9bc1e6c73c90b481eb39d357a232d12694c4b1a782e914707139486f67728431`

## Category Breakdown

| Category | Seed Count | Percentage of Divergent |
|---|---|---|
| `PRNG_CANDIDATE_SHIFT` | 3,113 | 100.00% |
| `PRNG_CANDIDATE_REPLACED` | 3,056 | 98.17% |
| `NEW_HAIRSTYLE_SAMPLED` | 386 | 12.40% |
| `NEW_JEWELRY_SAMPLED` | 199 | 6.39% |

## Invariant Non-Expanded Slots Verification

- All non-expanded slots (`clothing`, `props`, `character`, `makeup`, `pose`, `expression`, `tattoo`, `liquids`, `film`, `shot_type`, `camera_angle`, `nudity`, `imperfections`, `scene_theme`, `quality`):
  **0 drifts, 0 mutations across 10,000 seeds**.
- M1 6 assets backward compatibility verified: **100% clean generation**.
