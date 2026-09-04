"""
benchmark_24_atom.py — 24-Atom 性能基准测试脚本 (10 预热 + 200 正式迭代 + 1,000 补充样本)
"""
from __future__ import annotations

import hashlib
import statistics
import time
from pathlib import Path
from random import Random
import sys

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.conflict_resolver import ConflictResolver
from lib.models import PromptAtom, SelectionOrigin, SemanticFacts, SpanType, TagProvenance

DATA_DIR = REPO_ROOT / "data"


def make_atom(text: str, slot: str, idx: int, facts: SemanticFacts | None = None) -> PromptAtom:
    return PromptAtom(
        atom_id=f"atom_{idx:03d}",
        text=text,
        span_type=SpanType.PLAIN,
        source_slot=slot,
        tag_order=idx,
        span_order=idx,
        source_item_id=f"item_{idx:03d}",
        provenance=TagProvenance(item_id=f"item_{idx:03d}", parent_ids=(f"src_{idx:03d}",)),
        origin=SelectionOrigin(mode="explicit", entry_point="generator", selector=slot, selected_id=f"item_{idx:03d}"),
        facts=facts or SemanticFacts(),
    )


SAMPLE_ATOMS_24 = [
    make_atom("classroom", "scene", 0, SemanticFacts(space_kind="indoor", venue_ids=("classroom",))),
    make_atom("teacher student romance", "theme", 1, SemanticFacts(semantic_role="selector", venue_ids=("classroom",))),
    make_atom("close-up", "shot_type", 2, SemanticFacts(visible_regions=("face",))),
    make_atom("eye level angle", "camera_angle", 3, SemanticFacts(gaze="neutral")),
    make_atom("masterpiece", "quality", 4, SemanticFacts(quality_class="masterpiece")),
    make_atom("completely naked", "nudity", 5, SemanticFacts(visible_regions=("full_body",), garment_topologies=("none",))),
    make_atom("wearing uniform", "clothing", 6, SemanticFacts(garment_topologies=("one_piece",), garment_states=("worn",))),
    make_atom("neatly worn uniform", "clothing", 7, SemanticFacts(garment_topologies=("one_piece",), garment_states=("worn",))),
    make_atom("high heels", "clothing", 8, SemanticFacts(visible_regions=("feet",), garment_topologies=("bottom_pants",))),
    make_atom("long straight black hair", "hairstyle", 9, SemanticFacts(semantic_role="selector")),
    make_atom("blindfold", "jewelry", 10, SemanticFacts(occlusion="eyes")),
    make_atom("natural makeup", "makeup", 11, SemanticFacts(makeup_base="natural")),
    make_atom("hands behind back", "pose", 12, SemanticFacts(hand_state="both_busy", hands_required=2)),
    make_atom("shy expression", "expression", 13, SemanticFacts(emotion="shy", gaze="down")),
    make_atom("seductive smile", "expression", 14, SemanticFacts(emotion="seductive", gaze="camera")),
    make_atom("looking at viewer", "expression", 15, SemanticFacts(emotion="seductive", gaze="camera")),
    make_atom("soft daylight through window", "lighting", 16, SemanticFacts(time_of_day="day", light_sources=("daylight",), color_modes=("color",))),
    make_atom("classic monochrome", "film", 17, SemanticFacts(color_modes=("monochrome",))),
    make_atom("neon rim light", "lighting", 18, SemanticFacts(light_sources=("neon",), color_modes=("high_saturation",))),
    make_atom("cum on closed eyes", "liquids", 19, SemanticFacts(liquid_kind="sexual_fluid", liquid_locations=("face",), liquid_amount="normal")),
    make_atom("dragon tattoo on back", "tattoo", 20, SemanticFacts(visible_regions=("upper_body",))),
    make_atom("holding smartphone", "props", 21, SemanticFacts(prop_usage="handheld", hands_required=1)),
    make_atom("holding camera", "props", 22, SemanticFacts(prop_usage="handheld", hands_required=1)),
    make_atom("clean skin texture", "imperfections", 23, SemanticFacts(quality_class="masterpiece")),
]


def compute_fixture_sha(atoms: list[PromptAtom]) -> str:
    h = hashlib.sha256()
    for a in atoms:
        h.update(f"{a.atom_id}:{a.text}:{a.source_slot}:{a.origin.mode}:{sorted(vars(a.facts).items())}".encode("utf-8"))
    return h.hexdigest()


def run_benchmark(iterations: int, resolver: ConflictResolver) -> list[float]:
    durations_ms: list[float] = []
    for i in range(iterations):
        rng = Random(i)
        t0 = time.perf_counter()
        resolver.resolve_atoms_with_full_report(SAMPLE_ATOMS_24, rng)
        t1 = time.perf_counter()
        durations_ms.append((t1 - t0) * 1000.0)
    return durations_ms


def report_stats(label: str, durations_ms: list[float]) -> dict[str, float]:
    sorted_durations = sorted(durations_ms)
    n = len(sorted_durations)
    median_val = statistics.median(sorted_durations)
    p95_val = sorted_durations[int(n * 0.95)]
    p99_val = sorted_durations[int(n * 0.99)]
    mean_val = statistics.mean(sorted_durations)

    print(f"\n=== {label} ({n} iterations) ===")
    print(f"  Mean:   {mean_val:.3f} ms")
    print(f"  Median: {median_val:.3f} ms")
    print(f"  P95:    {p95_val:.3f} ms")
    print(f"  P99:    {p99_val:.3f} ms")

    return {
        "mean": mean_val,
        "median": median_val,
        "p95": p95_val,
        "p99": p99_val,
    }


def main():
    assert len(SAMPLE_ATOMS_24) == 24, f"Expected 24 atoms, got {len(SAMPLE_ATOMS_24)}"
    fixture_sha = compute_fixture_sha(SAMPLE_ATOMS_24)
    print(f"Formal 24-Atom Fixture SHA-256: {fixture_sha}")
    resolver = ConflictResolver(DATA_DIR)

    print("Warming up with 10 iterations...")
    for _ in range(10):
        resolver.resolve_atoms_with_full_report(SAMPLE_ATOMS_24, Random(42))

    # 1. Formal 200 iterations
    durations_200 = run_benchmark(200, resolver)
    stats_200 = report_stats("Formal 24-Atom Benchmark", durations_200)

    # 2. Supplementary 1,000 iterations
    durations_1000 = run_benchmark(1000, resolver)
    _ = report_stats("Supplementary Sample", durations_1000)

    if stats_200["p95"] <= 5.0:
        print("\nPASS: Formal 200-run P95 <= 5.0ms (P2 target met)")
    else:
        print(f"\nREGISTER P2: Formal 200-run P95 is {stats_200['p95']:.3f}ms > 5.0ms")


if __name__ == "__main__":
    main()
