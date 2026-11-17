"""Trinh dieu phoi Ca Quay Thuc dia Pha K0 ngoai cong (Preflight Check + Interactive/Batch Session Runner + Auto Eval).

Giup team mang laptop/server ra cong bam may tung kich ban trong `deploy/k0_session_plan.yaml` ma khong phai go tay
hang chuc tham so CLI, tu dong bo qua cac phien da quay xong (resume), kiem tra 0 audio (-an, H28), do GOP, va cuoi ca
tu dong chay `replay_eval.py` ket xuat `k0_report.json` + `K0_5_SUMMARY.md`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from server.config import load_config  # noqa: E402
from server.scripts.record_k0_bundle import export_gallery_refs, probe_stream_info, record_bundle  # noqa: E402
from server.scripts.replay_eval import evaluate_observations, render_k0_5_markdown_summary  # noqa: E402


def expand_session_plan(plan: dict) -> list[dict]:
    """Trien khai file YAML ke hoach ca quay thanh danh sach phien cu the co session_id duy nhat."""
    volunteers = plan.get("volunteers") or []
    stranger_consents = plan.get("stranger_consents") or []
    default_lux = float(plan.get("default_lux", 300.0))
    default_mode = str(plan.get("default_mode", "day_mode"))
    default_cap_sec = float(plan.get("default_capture_seconds", 8.0))
    default_max_frames = int(plan.get("default_max_frames", 300))

    expanded: list[dict] = []
    for spec in plan.get("sessions") or []:
        prefix = str(spec["id_prefix"])
        repeats = max(1, int(spec.get("repeats", 1)))
        base = {
            "title": str(spec.get("title") or prefix),
            "layout": str(spec.get("layout", "frontal_corridor")),
            "label": str(spec.get("label", "bonafide")),
            "pai_type": str(spec.get("pai_type", "")),
            "scenario": str(spec.get("scenario", "")),
            "distance_m": float(spec["distance_m"]) if spec.get("distance_m") is not None else None,
            "lux": float(spec.get("lux", default_lux)),
            "mode": str(spec.get("mode", default_mode)),
            "capture_seconds": float(spec.get("capture_seconds", default_cap_sec)),
            "max_frames": int(spec.get("max_frames", default_max_frames)),
        }

        if spec.get("per_volunteer"):
            for vol in volunteers:
                code = str(vol["code"]).strip()
                consent = str(vol["consent_ref"]).strip()
                for r_idx in range(1, repeats + 1):
                    expanded.append({
                        **base,
                        "session_id": f"{prefix}_{code.lower()}_r{r_idx:02d}",
                        "subject_code": code,
                        "consent_ref": consent,
                    })
        elif spec.get("pair_volunteers") and len(volunteers) >= 2:
            codes = ",".join(str(v["code"]).strip() for v in volunteers[:2])
            consents = "+".join(str(v["consent_ref"]).strip() for v in volunteers[:2])
            for r_idx in range(1, repeats + 1):
                expanded.append({
                    **base,
                    "session_id": f"{prefix}_r{r_idx:02d}",
                    "subject_code": codes,
                    "consent_ref": consents,
                })
        elif spec.get("use_stranger_consents"):
            for s_idx, c_ref in enumerate(stranger_consents, start=1):
                for r_idx in range(1, repeats + 1):
                    expanded.append({
                        **base,
                        "session_id": f"{prefix}_s{s_idx:02d}_r{r_idx:02d}",
                        "subject_code": "",
                        "consent_ref": str(c_ref).strip(),
                    })
        else:
            subj = str(spec.get("subject_code", "")).strip()
            consent = str(spec.get("consent_ref", "")).strip()
            for r_idx in range(1, repeats + 1):
                expanded.append({
                    **base,
                    "session_id": f"{prefix}_r{r_idx:02d}",
                    "subject_code": subj,
                    "consent_ref": consent,
                })
    return expanded


def run_preflight_checks(source: str, out_dir: Path, cfg: dict, min_free_gb: float = 2.0) -> dict:
    """Kiem tra dieu kien tien quyet truoc khi bam may ngoai cong (dung luong dia, Gallery, ffprobe 0 audio, GOP)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    free_gb = round(shutil.disk_usage(out_dir).free / (1024 ** 3), 2)
    gal_path = export_gallery_refs(cfg, out_dir)
    gal_count = 0
    gal_codes: list[str] = []
    if gal_path and gal_path.is_file():
        entries = json.loads(gal_path.read_text(encoding="utf-8")).get("entries", [])
        gal_count = len(entries)
        gal_codes = sorted({str(e.get("code")) for e in entries if e.get("code")})

    stream_info = (
        probe_stream_info(source)
        if source and not source.isdigit()
        else {"has_audio": False, "width": None, "height": None, "fps": None, "gop_seconds": None}
    )
    warnings: list[str] = []
    if free_gb < min_free_gb:
        warnings.append(f"Dung luong dia trong thap ({free_gb} GB < {min_free_gb} GB)")
    if gal_count == 0:
        warnings.append("Gallery hien dang rong — hay dang ky mau khuon mat cho tinh nguyen vien truoc khi quay K0")
    if stream_info.get("has_audio"):
        warnings.append("CANH BAO H28: Nguon video van chua kenh am thanh! Hay qua MediaMTX hoac bat --capture-seconds de cat sach (-an)")
    if stream_info.get("width") and int(stream_info["width"]) < 1920:
        warnings.append(f"Do phan giai nguon ({stream_info['width']}x{stream_info['height']}) thap hon luong /stream1 3MP (2304x1296)")

    return {
        "ok": len(warnings) == 0 or (free_gb >= min_free_gb and not stream_info.get("has_audio")),
        "free_disk_gb": free_gb,
        "gallery_embeddings": gal_count,
        "gallery_subjects": gal_codes,
        "stream": stream_info,
        "warnings": warnings,
    }


def plan_progress(tasks: list[dict], out_dir: Path) -> dict:
    mdir = out_dir / "manifests"
    done = [t["session_id"] for t in tasks if (mdir / f"{t['session_id']}.json").is_file()]
    pending = [t for t in tasks if t["session_id"] not in set(done)]
    return {"total": len(tasks), "completed": len(done), "remaining": len(pending), "pending_tasks": pending}


def finalize_k0_evaluation(out_dir: Path, threshold: float = 0.40, fas_threshold: float = 0.70,
                           calibrate: bool = True) -> tuple[Path, Path]:
    obs_path = out_dir / "observations.jsonl"
    if not obs_path.is_file():
        raise FileNotFoundError(f"Chua co {obs_path} de danh gia")
    evaluate_observations._calibrate_llr = bool(calibrate)
    report = evaluate_observations(obs_path, threshold=threshold, fas_threshold=fas_threshold)
    json_out = out_dir / "k0_report.json"
    md_out = out_dir / "K0_5_SUMMARY.md"
    json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_out.write_text(render_k0_5_markdown_summary(report, out_dir), encoding="utf-8")
    return json_out, md_out


def main() -> None:
    ap = argparse.ArgumentParser(description="Dieu phoi thu thap du lieu thuc dia K0 theo ke hoach YAML")
    ap.add_argument("--plan", default="deploy/k0_session_plan.yaml", help="File YAML ke hoach cac phien quay")
    ap.add_argument("--source", default="rtsp://127.0.0.1:8554/gate1_main", help="RTSP URL hoac file/thu muc video")
    ap.add_argument("--out", default="", help="Ghi de thu muc luu bundle (mac dinh doc tu file plan)")
    ap.add_argument("--status", action="store_true", help="Chi in tien do cac phien da quay / con thieu")
    ap.add_argument("--preflight-only", action="store_true", help="Chi chay kiem tra ha tang camera/dia/gallery")
    ap.add_argument("--eval-only", action="store_true", help="Chi chay danh gia Replay + ket xuat bao cao K0.5")
    ap.add_argument("--filter", default="", help="Chi chay cac phien co session_id chua chuoi nay (vd: pai_print)")
    ap.add_argument("--limit", type=int, default=0, help="Gioi han so phien quay trong lan chay nay (0 = tat ca)")
    ap.add_argument("--non-interactive", action="store_true", help="Chay lien tuc khong cho nhan Enter (dung cho auto/test)")
    ap.add_argument("--countdown", type=int, default=3, help="Dem nguoc N giay truoc khi ghi moi phien")
    args = ap.parse_args()

    cfg = load_config()
    plan_data = yaml.safe_load(Path(args.plan).read_text(encoding="utf-8"))
    out_dir = Path(args.out) if args.out else Path(plan_data.get("bundle_dir", "data/k0_bundle"))
    delete_raw = bool(plan_data.get("delete_raw_after_extract", False))
    tasks = expand_session_plan(plan_data)
    if args.filter:
        tasks = [t for t in tasks if args.filter in t["session_id"]]

    prog = plan_progress(tasks, out_dir)
    if args.status:
        print(json.dumps({k: v for k, v in prog.items() if k != "pending_tasks"}, indent=2, ensure_ascii=False))
        for t in prog["pending_tasks"][:15]:
            print(f"  [CHO QUAY] {t['session_id']} | {t['layout']} | {t['label']} {t['pai_type']} | {t['title']}")
        return

    if args.eval_only:
        j_path, m_path = finalize_k0_evaluation(out_dir)
        print(f"Da xuat bao cao JSON: {j_path}\nDa xuat bang tom tat K0.5: {m_path}")
        return

    pf = run_preflight_checks(args.source, out_dir, cfg)
    print("=== KIEM TRA TIEN DE K0 (PREFLIGHT) ===")
    print(json.dumps(pf, indent=2, ensure_ascii=False))
    if args.preflight_only:
        return

    pending = prog["pending_tasks"]
    if args.limit > 0:
        pending = pending[:args.limit]

    if not pending:
        print("Tat ca cac phien trong ke hoach da hoan tat! Dang cap nhat bao cao K0.5...")
        j_path, m_path = finalize_k0_evaluation(out_dir)
        print(f"Hoan tat: {j_path} | {m_path}")
        return

    is_live_net = args.source.lower().startswith(("rtsp://", "rtsps://", "rtmp://", "http://", "https://"))
    for idx, task in enumerate(pending, start=1):
        print(f"\n[{idx}/{len(pending)}] Phien: {task['session_id']}")
        print(f"  Mo ta   : {task['title']}")
        print(f"  Bo cuc  : {task['layout']} | Nhan: {task['label']} ({task['pai_type'] or 'n/a'}) | Doi tuong: {task['subject_code'] or 'stranger'}")
        print(f"  Cu ly   : {task['distance_m']}m | Anh sang: {task['lux']} lux | Don dong y: {task['consent_ref']}")
        if not args.non_interactive:
            ans = input("  -> San sang ngoai cong? [Enter = Bam may / s = Bo qua / q = Dung ca quay]: ").strip().lower()
            if ans == "q":
                break
            if ans == "s":
                continue
            for sec in range(max(0, args.countdown), 0, -1):
                print(f"  Bam may sau {sec}s...", end="\r", flush=True)
                time.sleep(1.0)
            print("  [DANG GHI HINH & TRICH XUAT...]        ")

        cap_sec = task["capture_seconds"] if is_live_net else 0.0
        manifest = record_bundle(
            video_source=args.source,
            out_dir=out_dir,
            layout=task["layout"],
            lux=task["lux"],
            mode=task["mode"],
            label=task["label"],
            pai_type=task["pai_type"],
            subject_code=task["subject_code"],
            max_frames=task["max_frames"],
            session_id=task["session_id"],
            distance_m=task["distance_m"],
            scenario=task["scenario"],
            consent_ref=task["consent_ref"],
            capture_seconds=cap_sec,
            delete_raw=delete_raw,
            export_gallery=False,
            cfg=cfg,
        )
        print(
            f"  [XONG] Frames={manifest['frames_recorded']} | Faces={manifest['faces_detected']} | "
            f"Blur={manifest['blur_median']} | Width={manifest['face_width_median_px']}px | AudioStripped={manifest['audio_stripped']}"
        )

    if (out_dir / "observations.jsonl").is_file():
        j_path, m_path = finalize_k0_evaluation(out_dir)
        print(f"\n=== CAP NHAT BAO CAO K0 HOAN TAT ===\n  - JSON: {j_path}\n  - Bang K0.5: {m_path}")


if __name__ == "__main__":
    main()
