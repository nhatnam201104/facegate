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
    # Danh gia FRR & chuan bi mau calibrate LLR 4 tham so (T042)
    frr_by_band = {"frontal_le40": [0, 0], "profile_gt40": [0, 0]}
    llr_samples: list[tuple[float, float, float, int]] = []
    centroids = {
        subj: (np.mean(vecs, axis=0) / (np.linalg.norm(np.mean(vecs, axis=0)) + 1e-9)).astype(np.float32)
        for subj, vecs in embs_by_subj.items() if vecs
    }
    for r in rows:
        gt = r.get("gt_label", "bonafide")
        subj = r.get("subject_code") or ""
        for f in r.get("faces", []):
            vec = np.asarray(f.get("embedding", []), dtype=np.float32)
            if vec.size == 0:
                continue
            vec = vec / (np.linalg.norm(vec) + 1e-9)
            q = f.get("quality", {})
            yaw = float(q.get("yaw", f.get("yaw", 0.0)))
            q_score = float(q.get("score", 0.8))
            band = "frontal_le40" if abs(yaw) <= 40.0 else "profile_gt40"
            if gt == "bonafide" and subj and subj in centroids:
                sim_pos = float(vec @ centroids[subj])
                frr_by_band[band][1] += 1
                if sim_pos < threshold:
                    frr_by_band[band][0] += 1
                llr_samples.append((sim_pos, yaw, q_score, 1))
                # Lay 1 impostor gan nhat lam mau am (y=0)
                neg_sims = [float(vec @ c) for osj, c in centroids.items() if osj != subj]
                if neg_sims:
                    llr_samples.append((max(neg_sims), yaw, q_score, 0))

    out_report = {
        "frames_evaluated": len(rows),
        "decode_ms_mean": round(float(np.mean(decode_ms)), 2) if decode_ms else 0.0,
        "decode_ms_p90": round(float(np.percentile(decode_ms, 90)), 2) if decode_ms else 0.0,
        "blur_median_by_layout": {k: round(float(np.median(v)), 2) for k, v in blur_by_layout.items() if v},
        "embedding_far": pairwise_impostor_far(embs_by_subj, threshold=threshold),
        "frr_by_yaw_band": {
            b: {
                "trials": tot,
                "false_rejects": err,
                "frr": round(err / tot, 4) if tot else 0.0,
                "frr_ci95_upper": wilson_upper_95(err, tot),
            }
            for b, (err, tot) in frr_by_band.items()
        },
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
    if getattr(evaluate_observations, "_calibrate_llr", False) and llr_samples:
        from app.decision import LLRConfig, compute_sample_llr, fit_llr_logistic_4param
        weights = fit_llr_logistic_4param(llr_samples)
        lcfg = LLRConfig(**weights)
        neg_scores = sorted(compute_sample_llr(s, y, q, lcfg) for s, y, q, lbl in llr_samples if lbl == 0)
        tau_rec = 0.45
        if neg_scores:
            # Chon tau nho nhat sao cho FAR tren mau am dat <= 0.1% (hoac vuot max neg)
            idx = min(len(neg_scores) - 1, max(0, int(math.ceil(0.999 * len(neg_scores))) - 1))
            tau_rec = round(float(neg_scores[idx] + 0.05), 4)
        out_report["calibrated_llr"] = {
            "llr_weights": weights,
            "tau_threshold": tau_rec,
            "samples": len(llr_samples),
        }
    return out_report


def main() -> None:
    ap = argparse.ArgumentParser(description="Chay Replay ngoai tuyen tren goi K0")
    ap.add_argument("bundle_dir", help="Thu muc chua observations.jsonl")
    ap.add_argument("--threshold", type=float, default=0.42)
    ap.add_argument("--fas-threshold", type=float, default=0.70)
    ap.add_argument("--calibrate-llr", action="store_true", help="Fit LLR 4 tham so tu observations.jsonl")
    ap.add_argument("--out", default="", help="Duong dan ghi JSON ket qua")
    args = ap.parse_args()
    obs = Path(args.bundle_dir) / "observations.jsonl"
    evaluate_observations._calibrate_llr = bool(args.calibrate_llr)
    report = evaluate_observations(obs, threshold=args.threshold, fas_threshold=args.fas_threshold)
    txt = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
