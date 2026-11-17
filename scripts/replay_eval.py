"""Cong cu Replay ngoai tuyen & Danh gia dinh luong K0 (Frame-level + Track-level Stateful Replay + Calibrate LLR v4)."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.context_detector import ContextScene, evaluate_face_context  # noqa: E402
from app.decision import LLRConfig, TrackDecisionAccumulator, compute_sample_llr, fit_llr_logistic_4param  # noqa: E402
from app.engines import Face  # noqa: E402
from app.tracker import IoUTracker  # noqa: E402


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


def _infer_pose_group(yaw: float, explicit_group: str | None = None, frontal_max: float = 40.0) -> str:
    if explicit_group in ("frontal", "left_profile", "right_profile"):
        return explicit_group
    if yaw < -frontal_max:
        return "left_profile"
    if yaw > frontal_max:
        return "right_profile"
    return "frontal"


def _build_reference_bank(
    rows: list[dict],
    gallery_refs_path: Path | None = None,
    frontal_max: float = 40.0,
) -> tuple[dict[str, int], dict[int, str], list[tuple[int, str, np.ndarray]]]:
    """Dung ngan hang mau Gallery dong bo voi Gallery.all_employee_similarities (uu tien gallery_refs.json neu co)."""
    code_to_id: dict[str, int] = {}
    id_to_code: dict[int, str] = {}
    entries: list[tuple[int, str, np.ndarray]] = []

    if gallery_refs_path is not None and gallery_refs_path.is_file():
        try:
            payload = json.loads(gallery_refs_path.read_text(encoding="utf-8"))
            for item in payload.get("entries", []):
                code = str(item.get("code") or "").strip()
                vec = np.asarray(item.get("vec", []), dtype=np.float32)
                if not code or vec.size == 0:
                    continue
                vec = vec / (np.linalg.norm(vec) + 1e-9)
                eid = int(item.get("employee_id") or (len(code_to_id) + 1))
                code_to_id.setdefault(code, eid)
                eid = code_to_id[code]
                id_to_code[eid] = code
                grp = str(item.get("pose_group") or "frontal")
                entries.append((eid, grp, vec))
        except Exception:  # noqa: BLE001
            entries.clear()

    # Bo sung tu chinh cac phien bonafide trong observations.jsonl cho nhan vien chua co trong gallery_refs.json
    by_subj_pose: dict[tuple[str, str], list[np.ndarray]] = {}
    for r in rows:
        if r.get("gt_label", "bonafide") != "bonafide":
            continue
        subj = str(r.get("subject_code") or "").strip()
        if not subj or "," in subj:
            continue
        if subj in code_to_id:
            continue
        for f in r.get("faces", []):
            vec = np.asarray(f.get("embedding", []), dtype=np.float32)
            if vec.size == 0:
                continue
            vec = vec / (np.linalg.norm(vec) + 1e-9)
            yaw = float(f.get("yaw", f.get("quality", {}).get("yaw", 0.0)))
            grp = _infer_pose_group(yaw, f.get("pose_group"), frontal_max=frontal_max)
            by_subj_pose.setdefault((subj, grp), []).append(vec)

    for (subj, grp), vecs in sorted(by_subj_pose.items()):
        if subj not in code_to_id:
            eid = max(id_to_code.keys(), default=0) + 1
            code_to_id[subj] = eid
            id_to_code[eid] = subj
        eid = code_to_id[subj]
        # Lay toi da 3 mau dai dien nhat (hoac trung binh chuan hoa theo pose_group) giong per_bin cua enrollment
        step = max(1, len(vecs) // 3)
        sampled = vecs[::step][:3]
        for sv in sampled:
            entries.append((eid, grp, sv))

    return code_to_id, id_to_code, entries


def _gallery_similarities_like_runtime(
    emb: np.ndarray,
    pose_group: str,
    entries: list[tuple[int, str, np.ndarray]],
    exclude_entry_vec: np.ndarray | None = None,
) -> dict[int, float]:
    """Mo phong chinh xac Gallery.all_employee_similarities(emb, pose_group) cua runtime (loai bo trung khop chinh frame do)."""
    if not entries:
        return {}
    best: dict[int, float] = {}
    for eid, grp, ref_vec in entries:
        if ref_vec.size != emb.size:
            continue
        if exclude_entry_vec is not None and float(ref_vec @ exclude_entry_vec) > 0.99999:
            continue
        if pose_group and pose_group != "frontal" and grp not in (pose_group, "frontal"):
            continue
        s = float(ref_vec @ emb)
        if s > best.get(eid, -1.0):
            best[eid] = s
    if not best:
        for eid, _, ref_vec in entries:
            if ref_vec.size != emb.size:
                continue
            if exclude_entry_vec is not None and float(ref_vec @ exclude_entry_vec) > 0.99999:
                continue
            s = float(ref_vec @ emb)
            if s > best.get(eid, -1.0):
                best[eid] = s
    return best


def _synthetic_bbox_for_legacy_face(idx: int, frame_w: int, frame_h: int) -> np.ndarray:
    x1 = 120.0 + idx * 240.0
    y1 = 140.0
    return np.array([x1, y1, min(frame_w - 10.0, x1 + 140.0), min(frame_h - 10.0, y1 + 160.0)], dtype=np.float32)


def replay_sessions_stateful(
    rows: list[dict],
    entries: list[tuple[int, str, np.ndarray]],
    id_to_code: dict[int, str],
    llr_cfg: LLRConfig | None = None,
    fas_threshold: float = 0.70,
    spoof_below: float = 0.35,
    fas_min_frames: int = 4,
    fas_window: int = 8,
    require_frontal_for_checkin: bool = True,
) -> dict:
    """Chay Track-level Stateful Replay (IoUTracker + TrackDecisionAccumulator + cua so MiniFASNet + Context) tren tung session."""
    llr_cfg = llr_cfg or LLRConfig()
    sessions: dict[str, list[dict]] = {}
    for idx, r in enumerate(rows):
        sid = str(
            r.get("session_id")
            or f"legacy_{r.get('layout', 'unk')}_{r.get('gt_label', 'bonafide')}_{r.get('pai_type', '')}_{r.get('subject_code', idx)}"
        )
        sessions.setdefault(sid, []).append(r)

    track_apcer_trials: dict[str, list[bool]] = {}
    bonafide_sessions_total = 0
    bonafide_checkin_correct = 0
    bonafide_false_ident = 0
    bonafide_rejected_by_liveness = 0
    stranger_sessions_total = 0
    stranger_false_accepts = 0
    crossing_sessions_total = 0
    crossing_false_checkins = 0
    session_details: list[dict] = []

    for sid, s_rows in sessions.items():
        s_rows = sorted(s_rows, key=lambda x: int(x.get("frame_idx", 0)))
        gt = str(s_rows[0].get("gt_label", "bonafide"))
        pai = str(s_rows[0].get("pai_type") or "unspecified_pai")
        subj_raw = str(s_rows[0].get("subject_code") or "").strip()
        allowed_subjects = {s.strip() for s in subj_raw.split(",") if s.strip()}
        scenario = str(s_rows[0].get("scenario") or "")

        tracker = IoUTracker(max_age=2.0, crossing_face_iou=0.25, crossing_person_iou=0.40)
        committed_codes: list[str] = []
        spoof_flagged = False
        had_identified_locked = False

        for step_i, r in enumerate(s_rows):
            fsize = r.get("frame_size") or [2304, 1296]
            fw_img, fh_img = int(fsize[0]), int(fsize[1])
            if "video_ts_ms" in r:
                now_sec = float(r["video_ts_ms"]) / 1000.0
            else:
                now_sec = step_i * (1.0 / 6.0)

            pboxes = [np.asarray(pb, dtype=np.float32) for pb in (r.get("person_boxes") or []) if len(pb) >= 4]
            mboxes = [
                (np.asarray(m["bbox"], dtype=np.float32), str(m.get("label", "cell phone")), float(m.get("score", 0.5)))
                for m in (r.get("medium_boxes") or [])
                if isinstance(m, dict) and len(m.get("bbox") or []) >= 4
            ]
            has_scene_data = ("person_boxes" in r) or ("medium_boxes" in r)
            scene = ContextScene(persons=pboxes, mediums=mboxes) if has_scene_data else None

            raw_faces = r.get("faces") or []
            det_faces: list[Face] = []
            for fi, fd in enumerate(raw_faces):
                bb = fd.get("bbox")
                bbox_arr = (
                    np.asarray(bb, dtype=np.float32)
                    if bb and len(bb) == 4
                    else _synthetic_bbox_for_legacy_face(fi, fw_img, fh_img)
                )
                det_faces.append(Face(bbox_arr, float(fd.get("det_score", 0.9)), np.zeros((5, 2), dtype=np.float32)))

            matched, _ = tracker.update(det_faces, now=now_sec, person_boxes=pboxes if pboxes else None)
            face_dict_by_center = {
                (round(float(df.bbox[0] + df.bbox[2]) * 0.5, 1), round(float(df.bbox[1] + df.bbox[3]) * 0.5, 1)): fd
                for df, fd in zip(det_faces, raw_faces)
            }

            for t, f_obj in matched:
                key = (round(float(f_obj.bbox[0] + f_obj.bbox[2]) * 0.5, 1), round(float(f_obj.bbox[1] + f_obj.bbox[3]) * 0.5, 1))
                fd = face_dict_by_center.get(key, {})
                if t.decision_acc is None:
                    t.decision_acc = TrackDecisionAccumulator(cfg=llr_cfg)
                acc: TrackDecisionAccumulator = t.decision_acc  # type: ignore[assignment]

                q_dict = fd.get("quality") or {}
                q_ok = bool(q_dict.get("ok", True))
                q_score = float(q_dict.get("score", 0.85))
                yaw = float(fd.get("yaw", q_dict.get("yaw", 0.0)))
                pose_grp = _infer_pose_group(yaw, fd.get("pose_group"), frontal_max=llr_cfg.frontal_max_yaw)

                ctx_eval = fd.get("context")
                if ctx_eval is None:
                    ctx_eval = evaluate_face_context(
                        f_obj.bbox, scene, frame_height=fh_img, viewport_bottom_grace=0.80,
                    )
                if ctx_eval.get("spoof_reason") == "screen_detected":
                    t.spoof = True
                    t.spoof_reason = "screen_detected"
                elif ctx_eval.get("spoof_reason") == "unattached_face":
                    t.unattached_streak += 1
                    if t.unattached_streak >= 4:
                        t.spoof = True
                        t.spoof_reason = t.spoof_reason or "unattached_face"
                else:
                    t.unattached_streak = 0

                vec = np.asarray(fd.get("embedding") or [], dtype=np.float32)
                if vec.size > 0 and q_ok and not t.crossing_active and not t.spoof:
                    vec = vec / (np.linalg.norm(vec) + 1e-9)
                    agg = t.agg_embedding()
                    if len(t.embs) >= 2 and agg is not None and float(vec @ agg) < 0.25:
                        t = tracker.split(t, f_obj, now_sec)
                        t.decision_acc = TrackDecisionAccumulator(cfg=llr_cfg)
                        acc = t.decision_acc
                    t.embs.append((vec, max(q_score, 0.05)))
                    t.embs = t.embs[-10:]
                    sims = _gallery_similarities_like_runtime(vec, pose_grp, entries)
                    acc.observe(now_sec * 1000.0, yaw, q_score, sims)
                    if acc.locked_employee_id is not None:
                        t.employee_id = acc.locked_employee_id
                        t.code = id_to_code.get(acc.locked_employee_id)
                        t.quarantine = False
                        had_identified_locked = True
                    elif t.employee_id is not None and not t.event_done:
                        t.employee_id = None
                        t.code = None

                fas_prob = fd.get("fas_prob")
                if fas_prob is not None and q_ok and abs(yaw) <= llr_cfg.frontal_max_yaw and not t.spoof:
                    t.live.append(float(fas_prob))
                    t.live = t.live[-fas_window:]
                    if len(t.live) >= fas_min_frames and float(np.mean(t.live)) < spoof_below:
                        t.spoof = True
                        t.spoof_reason = t.spoof_reason or "texture_spoof"

                if t.spoof:
                    live_status = "spoof"
                    spoof_flagged = True
                elif fas_prob is None and not t.live:
                    live_status = "live"
                elif len(t.live) >= fas_min_frames and float(np.mean(t.live)) >= fas_threshold:
                    live_status = "live"
                elif (not require_frontal_for_checkin) and abs(yaw) > llr_cfg.frontal_max_yaw and ctx_eval.get("body_coupled", True):
                    live_status = "live"
                else:
                    live_status = "checking"

                state_str = acc.apply_liveness(live_status)
                if state_str == "LIVE_CONFIRMED" and not t.event_done and not t.quarantine and t.code:
                    acc.mark_committed()
                    t.event_done = True
                    committed_codes.append(t.code)

        if gt == "spoof":
            # Mot phien spoof bi coi la LOT (attack passed) neu he thong phat checkin HOAC vuot qua liveness ma khong bi chan
            spoof_passed = len(committed_codes) > 0
            track_apcer_trials.setdefault(pai, []).append(spoof_passed)
        elif gt == "bonafide":
            bonafide_sessions_total += 1
            wrong_codes = [c for c in committed_codes if allowed_subjects and c not in allowed_subjects]
            if wrong_codes:
                bonafide_false_ident += 1
            if scenario == "crossing":
                crossing_sessions_total += 1
                if wrong_codes:
                    crossing_false_checkins += 1
            if allowed_subjects and any(c in allowed_subjects for c in committed_codes):
                bonafide_checkin_correct += 1
            elif spoof_flagged or had_identified_locked:
                bonafide_rejected_by_liveness += 1
        elif gt == "stranger":
            stranger_sessions_total += 1
            if len(committed_codes) > 0:
                stranger_false_accepts += 1

        session_details.append({
            "session_id": sid,
            "gt_label": gt,
            "pai_type": pai if gt == "spoof" else "",
            "scenario": scenario,
            "expected_subjects": sorted(allowed_subjects),
            "committed_codes": committed_codes,
            "spoof_flagged": spoof_flagged,
        })

    track_apcer_by_pai = {}
    for pai, trials in track_apcer_trials.items():
        passed = sum(1 for x in trials if x)
        n = len(trials)
        track_apcer_by_pai[pai] = {
            "sessions": n,
            "spoof_checkin_passed": passed,
            "apcer": round(passed / n, 4) if n else 0.0,
            "apcer_ci95_upper": wilson_upper_95(passed, n),
        }

    total_impostor_sessions = stranger_sessions_total + bonafide_sessions_total
    total_false_checkins = stranger_false_accepts + bonafide_false_ident
    bonafide_misses = max(0, bonafide_sessions_total - bonafide_checkin_correct)

    return {
        "sessions_evaluated": len(sessions),
        "event_far": {
            "trials": total_impostor_sessions,
            "false_checkins": total_false_checkins,
            "stranger_false_accepts": stranger_false_accepts,
            "employee_misidentifications": bonafide_false_ident,
            "far": round(total_false_checkins / total_impostor_sessions, 4) if total_impostor_sessions else 0.0,
            "far_ci95_upper": wilson_upper_95(total_false_checkins, total_impostor_sessions),
        },
        "event_frr": {
            "bonafide_sessions": bonafide_sessions_total,
            "missed_checkins": bonafide_misses,
            "frr": round(bonafide_misses / bonafide_sessions_total, 4) if bonafide_sessions_total else 0.0,
            "frr_ci95_upper": wilson_upper_95(bonafide_misses, bonafide_sessions_total),
        },
        "crossing_quarantine": {
            "crossing_sessions": crossing_sessions_total,
            "id_switch_false_checkins": crossing_false_checkins,
        },
        "iso_30107_3_track_level": {
            "apcer_by_pai": track_apcer_by_pai,
            "bpcer": {
                "trials": bonafide_sessions_total,
                "rejected_real_sessions": bonafide_rejected_by_liveness,
                "bpcer": round(bonafide_rejected_by_liveness / bonafide_sessions_total, 4) if bonafide_sessions_total else 0.0,
                "bpcer_ci95_upper": wilson_upper_95(bonafide_rejected_by_liveness, bonafide_sessions_total),
            },
        },
        "sessions": session_details,
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
    # Danh gia FRR & chuan bi mau calibrate LLR 4 tham so khop Gallery.all_employee_similarities theo pose_group (T042)
    frr_by_band = {"frontal_le40": [0, 0], "profile_gt40": [0, 0]}
    llr_samples: list[tuple[float, float, float, int]] = []
    gallery_refs_path = obs_path.parent / "gallery_refs.json"
    code_to_id, id_to_code, ref_entries = _build_reference_bank(rows, gallery_refs_path=gallery_refs_path, frontal_max=40.0)

    for r in rows:
        gt = r.get("gt_label", "bonafide")
        subj = str(r.get("subject_code") or "").strip()
        for f in r.get("faces", []):
            vec = np.asarray(f.get("embedding", []), dtype=np.float32)
            if vec.size == 0:
                continue
            vec = vec / (np.linalg.norm(vec) + 1e-9)
            q = f.get("quality", {})
            yaw = float(q.get("yaw", f.get("yaw", 0.0)))
            q_score = float(q.get("score", 0.8))
            pose_grp = _infer_pose_group(yaw, f.get("pose_group"), frontal_max=40.0)
            band = "frontal_le40" if abs(yaw) <= 40.0 else "profile_gt40"
            sims_by_eid = _gallery_similarities_like_runtime(vec, pose_grp, ref_entries, exclude_entry_vec=vec)

            if gt == "bonafide" and subj and "," not in subj and subj in code_to_id:
                own_eid = code_to_id[subj]
                if own_eid in sims_by_eid:
                    sim_pos = sims_by_eid[own_eid]
                    frr_by_band[band][1] += 1
                    if sim_pos < threshold:
                        frr_by_band[band][0] += 1
                    llr_samples.append((sim_pos, yaw, q_score, 1))
                neg_sims = [s for eid, s in sims_by_eid.items() if eid != own_eid]
                if neg_sims:
                    llr_samples.append((max(neg_sims), yaw, q_score, 0))
            elif gt == "stranger" and sims_by_eid:
                # Mau nguoi la ngoai doi thuc: lay diem giong cao nhat voi bat ky nhan vien nao trong Gallery lam mau am (y=0)
                llr_samples.append((max(sims_by_eid.values()), yaw, q_score, 0))

    track_report = replay_sessions_stateful(
        rows,
        ref_entries,
        id_to_code,
        fas_threshold=fas_threshold,
    )

    face_widths_by_layout: dict[str, list[float]] = {}
    infer_ms: list[float] = []
    for r in rows:
        if "infer_ms" in r:
            infer_ms.append(float(r["infer_ms"]))
        ly = r.get("layout", "unknown")
        for f in r.get("faces", []):
            bb = f.get("bbox")
            if bb and len(bb) == 4:
                face_widths_by_layout.setdefault(ly, []).append(float(bb[2]) - float(bb[0]))
            elif "size" in f.get("quality", {}):
                face_widths_by_layout.setdefault(ly, []).append(float(f["quality"]["size"]))

    out_report = {
        "frames_evaluated": len(rows),
        "decode_ms_mean": round(float(np.mean(decode_ms)), 2) if decode_ms else 0.0,
        "decode_ms_p90": round(float(np.percentile(decode_ms, 90)), 2) if decode_ms else 0.0,
        "infer_ms_mean": round(float(np.mean(infer_ms)), 2) if infer_ms else 0.0,
        "infer_ms_p90": round(float(np.percentile(infer_ms, 90)), 2) if infer_ms else 0.0,
        "blur_median_by_layout": {k: round(float(np.median(v)), 2) for k, v in blur_by_layout.items() if v},
        "face_width_median_by_layout": {k: round(float(np.median(v)), 1) for k, v in face_widths_by_layout.items() if v},
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
        "track_level": track_report,
    }
    if getattr(evaluate_observations, "_calibrate_llr", False) and llr_samples:
        weights = fit_llr_logistic_4param(llr_samples)
        lcfg = LLRConfig(**weights)
        neg_scores = sorted(compute_sample_llr(s, y, q, lcfg) for s, y, q, lbl in llr_samples if lbl == 0)
        tau_rec = 0.45
        if neg_scores:
            idx = min(len(neg_scores) - 1, max(0, int(math.ceil(0.999 * len(neg_scores))) - 1))
            tau_rec = round(float(neg_scores[idx] + 0.05), 4)
        lcfg_cal = LLRConfig(**weights, tau_threshold=tau_rec)
        cal_track = replay_sessions_stateful(rows, ref_entries, id_to_code, llr_cfg=lcfg_cal, fas_threshold=fas_threshold)
        out_report["calibrated_llr"] = {
            "llr_weights": weights,
            "tau_threshold": tau_rec,
            "samples": len(llr_samples),
            "track_level_after_calibration": {
                "event_far": cal_track["event_far"],
                "event_frr": cal_track["event_frr"],
            },
        }
    return out_report


def render_k0_5_markdown_summary(report: dict, bundle_dir: Path) -> str:
    """Ket xuat bang tom tat dinh luong san sang dien vao docs/BAO_CAO_K0_5_PM.md."""
    manifests_dir = bundle_dir / "manifests"
    gops: dict[str, list[float]] = {}
    dists: dict[str, list[float]] = {}
    audio_clean = True
    if manifests_dir.is_dir():
        for mp in sorted(manifests_dir.glob("*.json")):
            try:
                m = json.loads(mp.read_text(encoding="utf-8"))
                ly = str(m.get("layout", "unknown"))
                if m.get("gop_seconds") is not None:
                    gops.setdefault(ly, []).append(float(m["gop_seconds"]))
                if m.get("distance_m") is not None:
                    dists.setdefault(ly, []).append(float(m["distance_m"]))
                if not m.get("audio_stripped", True):
                    audio_clean = False
            except Exception:  # noqa: BLE001
                pass

    def _fmt_ly(d: dict, ly: str, default: str = "n/a") -> str:
        return str(d.get(ly, default))

    blur = report.get("blur_median_by_layout", {})
    fw = report.get("face_width_median_by_layout", {})
    efar = report.get("embedding_far", {})
    trk = report.get("track_level", {})
    iso_trk = trk.get("iso_30107_3_track_level", {})
    apcer_str = ", ".join(
        f"{pai}: {v['apcer']*100:.1f}% (CI95≤{v['apcer_ci95_upper']*100:.1f}%, n={v['sessions']})"
        for pai, v in sorted((iso_trk.get("apcer_by_pai") or {}).items())
    ) or "Chưa có phiên spoof"
    bpcer_v = iso_trk.get("bpcer", {})
    gop_sw = f"{np.median(gops['side_walk']):.2f}s" if gops.get("side_walk") else "n/a"
    gop_fc = f"{np.median(gops['frontal_corridor']):.2f}s" if gops.get("frontal_corridor") else "n/a"
    dist_sw = f"{np.median(dists['side_walk']):.2f}m" if dists.get("side_walk") else "n/a"
    dist_fc = f"{np.median(dists['frontal_corridor']):.2f}m" if dists.get("frontal_corridor") else "n/a"

    lines = [
        "# Tóm tắt Số liệu Thực nghiệm Pha `K0` (Tự động tổng hợp từ `replay_eval.py`)",
        "",
        f"- **Tổng số khung hình đã xử lý**: `{report.get('frames_evaluated', 0)}` · **Tổng số phiên (Track/Session)**: `{trk.get('sessions_evaluated', 0)}`",
        f"- **Cam kết 0 Audio (`-an`, H28)**: `{'ĐẠT' if audio_clean else 'CẢNH BÁO CÓ AUDIO'}`",
        "",
        "| Chỉ số đo kiểm | Đi ngang (`side_walk`, ±90°) | Nhìn thẳng (`frontal_corridor`, ±40°) | Mục tiêu Cổng |",
        "|---|---|---|---|",
        f"| Khoảng cách trung vị & Bề ngang mặt (`px` trên 3MP) | `{dist_sw}` / `{_fmt_ly(fw, 'side_walk')} px` | `{dist_fc}` / `{_fmt_ly(fw, 'frontal_corridor')} px` | `≥ 80 px` (`1.5–2.5 m`) |",
        f"| Độ nét trung vị (`Laplacian blur`) | `{_fmt_ly(blur, 'side_walk')}` | `{_fmt_ly(blur, 'frontal_corridor')}` | `≥ 35` |",
        f"| Khoảng cách Keyframe (`GOP`) thực tế | `{gop_sw}` | `{gop_fc}` | Đo thực tế C310 |",
        f"| Chi phí CPU (`decode_ms` + `infer_ms` TB / p90) | Decode `{report.get('decode_ms_mean', 0)} ms` (p90 `{report.get('decode_ms_p90', 0)} ms`) · Infer `{report.get('infer_ms_mean', 0)} ms` (p90 `{report.get('infer_ms_p90', 0)} ms`) | Dùng chung pipeline 3MP | Tổng chu kỳ `< 200 ms` |",
        f"| **FAR** cặp ghép chéo (`H12a`) | Điểm `{efar.get('far_point', 0)*100:.4f}%` · CI95≤`{efar.get('far_ci95_upper', 1)*100:.4f}%` (`{efar.get('impostor_pairs', 0)}` cặp) | Cùng tập nhân viên | `≤ 0.1%` (mức cặp) |",
        f"| **FAR / FRR** cấp sự kiện (`Track-level`) | FAR `{trk.get('event_far', {}).get('far', 0)*100:.2f}%` (CI95≤`{trk.get('event_far', {}).get('far_ci95_upper', 1)*100:.2f}%`) · FRR `{trk.get('event_frr', {}).get('frr', 0)*100:.2f}%` | Crossing ID-Switch: `{trk.get('crossing_quarantine', {}).get('id_switch_false_checkins', 0)}` ca | FAR `≤ 1%` · `0` ca tráo người |",
        f"| **APCER** cấp sự kiện theo PAI (`ISO 30107-3`) | {apcer_str} | Như cột bên | `≤ 10%` mỗi loại PAI |",
        f"| **BPCER** cấp sự kiện (`ISO 30107-3`) | `{bpcer_v.get('bpcer', 0)*100:.2f}%` (CI95≤`{bpcer_v.get('bpcer_ci95_upper', 1)*100:.2f}%`, `{bpcer_v.get('trials', 0)}` phiên) | Như cột bên | `≤ 5%` |",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Chay Replay ngoai tuyen (Frame + Track-level) tren goi K0")
    ap.add_argument("bundle_dir", help="Thu muc chua observations.jsonl")
    ap.add_argument("--threshold", type=float, default=0.42)
    ap.add_argument("--fas-threshold", type=float, default=0.70)
    ap.add_argument("--calibrate-llr", action="store_true", help="Fit LLR 4 tham so dong bo Gallery pose_group")
    ap.add_argument("--out", default="", help="Duong dan ghi JSON ket qua")
    ap.add_argument("--report-md", default="", help="Duong dan ghi bang Markdown tom tat cho cong K0.5")
    args = ap.parse_args()
    bundle = Path(args.bundle_dir)
    obs = bundle / "observations.jsonl"
    evaluate_observations._calibrate_llr = bool(args.calibrate_llr)
    report = evaluate_observations(obs, threshold=args.threshold, fas_threshold=args.fas_threshold)
    txt = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(txt, encoding="utf-8")
    if args.report_md:
        Path(args.report_md).write_text(render_k0_5_markdown_summary(report, bundle), encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
