# M1 Wildcards Vertical Slice Divergence Audit Report

- **Baseline Commit**: `c74084d` (`ab5a633cfb3fde70d9a6c629ee65a540955d87ee143e66570d780743246cab89`)
- **Current Target**: `420013a55408d6dbc4d6d0959f145c6850da7f2b` (`aa7581bc2304f6f75530d95ab4e1b75e7e1101720b1dfaf14c2a1c328fcd1139`)
- **Tree Hash**: `e7cfcdb90dcd06005165514747f32901b2d92796` (Dirty: False)
- **Total Seeds**: 10,000
- **Identical Seeds**: 5,201 (52.01%)
- **Divergent Seeds**: 4,799 (47.99%)
- **Unexplained Seeds**: **0** (Gate: == 0)
- **Evidence Archive**: `audit_m1_evidence.json.gz` (4,799 divergent records with full items and decisions)

## Category Breakdown

| Category | Seed Count | Percentage of Divergent |
|---|---|---|
| `PRNG_CANDIDATE_REPLACED` | 3,749 | 78.12% |
| `PRNG_CANDIDATE_SHIFT` | 3,498 | 72.89% |
| `NEW_HAIRSTYLE_SAMPLED` | 182 | 3.79% |
| `NEW_PROP_SAMPLED` | 162 | 3.38% |
| `RESOLVED_BY_RULE` | 93 | 1.94% |