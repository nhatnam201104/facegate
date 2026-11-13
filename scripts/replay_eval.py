"""Cong cu Replay ngoai tuyen & Danh gia dinh luong K0 (FAR cap cheo + Wilson CI + ISO/IEC 30107-3 APCER/BPCER)."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np


def wilson_upper_95(errors: int, total: int) -> float:
    """Can tren khoang tin cay 95% Wilson (hoac xap xi rule-of-three 3/n khi errors == 0)."""
    if total <= 0:
        return 1.0
    z = 1.959964
    p = errors / total
    denom = 1.0 + (z * z) / total
    center = p + (z * z) / (2.0 * total)
    rad = z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * total)) / total)
    return round(min(1.0, (center + rad) / denom), 6)


def pairwise_impostor_far(embeddings_by_subject: dict[str, list[np.ndarray]], threshold: float) -> dict:
    """Ghep cheo moi cap khac nguoi (H12a) de tao hang nghin cap impostor do FAR muc embedding."""
    subjects = sorted(k for k, v in embeddings_by_subject.items() if k and len(v) > 0)
    total_pairs = 0
    false_accepts = 0
    for i, s1 in enumerate(subjects):
        m1 = np.stack(embeddings_by_subject[s1]).astype(np.float32)
        for s2 in subjects[i + 1:]:
            m2 = np.stack(embeddings_by_subject[s2]).astype(np.float32)
            sims = m1 @ m2.T
            total_pairs += int(sims.size)
            false_accepts += int(np.sum(sims >= threshold))
    rate = (false_accepts / total_pairs) if total_pairs else 0.0
    return {
        "impostor_pairs": total_pairs,
        "false_accepts": false_accepts,
        "far_point": round(rate, 6),
        "far_ci95_upper": wilson_upper_95(false_accepts, total_pairs),
    }


def evaluate_observations(obs_path: Path, threshold: float = 0.42, fas_threshold: float = 0.70) -> dict:
    rows = [json.loads(line) for line in obs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    embs_by_subj: dict[str, list[np.ndarray]] = {}
    pai_trials: dict[str, list[bool]] = {}   # True = spoof accepted (attack passed)
    bonafide_trials: list[bool] = []         # True = bonafide rejected by liveness
    blur_by_layout: dict[str, list[float]] = {}
    decode_ms: list[float] = []

    for r in rows:
        if "decode_ms" in r:
            decode_ms.append(float(r["decode_ms"]))
        layout = r.get("layout", "unknown")
        gt = r.get("gt_label", "bonafide")
        pai = r.get("pai_type") or "unspecified_pai"
        subj = r.get("subject_code") or ""
        for f in r.get("faces", []):
            q = f.get("quality", {})
            if "blur" in q:
                blur_by_layout.setdefault(layout, []).append(float(q["blur"]))
            vec = np.asarray(f.get("embedding", []), dtype=np.float32)
            if vec.size > 0 and gt == "bonafide" and subj:
                norm = np.linalg.norm(vec) + 1e-9
                embs_by_subj.setdefault(subj, []).append(vec / norm)
            fas = f.get("fas_prob")
            if fas is not None:
                if gt == "spoof":
                    pai_trials.setdefault(pai, []).append(float(fas) >= fas_threshold)
                elif gt == "bonafide":
                    bonafide_trials.append(float(fas) < fas_threshold)

    apcer_by_pai = {}
    for pai, results in pai_trials.items():
        passed = sum(1 for x in results if x)
        n = len(results)
        apcer_by_pai[pai] = {
            "trials": n,
            "spoof_passed": passed,
            "apcer": round(passed / n, 4) if n else 0.0,
            "apcer_ci95_upper": wilson_upper_95(passed, n),
        }

    bp_err = sum(1 for x in bonafide_trials if x)
    bp_n = len(bonafide_trials)
    return {
        "frames_evaluated": len(rows),
        "decode_ms_mean": round(float(np.mean(decode_ms)), 2) if decode_ms else 0.0,
        "decode_ms_p90": round(float(np.percentile(decode_ms, 90)), 2) if decode_ms else 0.0,
        "blur_median_by_layout": {k: round(float(np.median(v)), 2) for k, v in blur_by_layout.items() if v},
        "embedding_far": pairwise_impostor_far(embs_by_subj, threshold=threshold),
        "iso_30107_3": {
            "apcer_by_pai": apcer_by_pai,
            "bpcer": {
                "trials": bp_n,
                "rejected_real": bp_err,
                "bpcer": round(bp_err / bp_n, 4) if bp_n else 0.0,
                "bpcer_ci95_upper": wilson_upper_95(bp_err, bp_n),
            },
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Chay Replay ngoai tuyen tren goi K0")
    ap.add_argument("bundle_dir", help="Thu muc chua observations.jsonl")
    ap.add_argument("--threshold", type=float, default=0.42)
    ap.add_argument("--fas-threshold", type=float, default=0.70)
    args = ap.parse_args()
    obs = Path(args.bundle_dir) / "observations.jsonl"
    report = evaluate_observations(obs, threshold=args.threshold, fas_threshold=args.fas_threshold)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
