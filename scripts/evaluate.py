"""Evaluate face detection + recognition on a labelled image dataset.

Dataset layout (one folder per person, any image names):

    dataset/
      BSCS-2023-001/ img1.jpg img2.jpg ...
      BSCS-2023-002/ ...

Protocol
  * For every "known" identity the first --enroll images build the gallery; the
    remaining images are probes.
  * --unknown-fraction of identities are NOT enrolled at all; all their images are
    "impostor" probes, used to measure how often an unregistered person is accepted.

Reported metrics
  * Detection   : detection rate (probe images with >= 1 face), mean faces/image,
                  and - if --annotations CSV (file,x,y,w,h) is given - precision / recall @ IoU 0.5
  * Recognition : rank-1 accuracy (closed set), and at the chosen threshold:
                  correct identification rate, FRR (known rejected), FAR (unknown accepted),
                  misidentification rate; threshold sweep, EER and a confusion matrix
  * Performance : detection / embedding latency and estimated FPS

Run separately per condition to compare (e.g. dataset_normal/, dataset_lowlight/, dataset_angles/):

    python scripts/evaluate.py --data dataset_normal --out results/normal
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.vision.backends import OpenCVBackend  # noqa: E402
from app.vision.matcher import Gallery  # noqa: E402
from app.vision.pipeline import iou  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def load_dataset(root: Path) -> dict[str, list[Path]]:
    data = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        imgs = sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXT)
        if imgs:
            data[d.name] = imgs
    return data


def largest(faces):
    return max(faces, key=lambda f: f.bbox[2] * f.bbox[3]) if faces else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--out", default="results", type=Path)
    ap.add_argument("--enroll", type=int, default=3, help="images per person used for enrollment")
    ap.add_argument("--unknown-fraction", type=float, default=0.2)
    ap.add_argument("--threshold", type=float, default=settings.match_threshold)
    ap.add_argument("--margin", type=float, default=settings.match_margin)
    ap.add_argument("--annotations", type=Path, help="CSV with columns file,x,y,w,h (ground-truth boxes)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    backend = OpenCVBackend(settings.models_dir, settings.detection_score_threshold, settings.min_face_size)
    data = load_dataset(args.data)
    if len(data) < 2:
        print("Need at least two identity folders")
        return 1
    rng = np.random.RandomState(args.seed)
    names = list(data)
    n_unknown = int(round(len(names) * args.unknown_fraction))
    unknown = set(rng.choice(names, n_unknown, replace=False)) if n_unknown else set()
    label_of = {n: i for i, n in enumerate(names)}

    det_times, emb_times, faces_per_img = [], [], []
    detected = total_imgs = 0
    gallery_vecs, gallery_labels = [], []
    probes: list[tuple[int, np.ndarray, bool]] = []  # (true label, embedding, is_known)
    det_boxes: dict[str, list] = {}

    for name, imgs in data.items():
        is_known = name not in unknown
        for idx, path in enumerate(imgs):
            img = cv2.imread(str(path))
            if img is None:
                continue
            total_imgs += 1
            t0 = time.perf_counter()
            faces = backend.detect(img)
            det_times.append(time.perf_counter() - t0)
            faces_per_img.append(len(faces))
            det_boxes[str(path.relative_to(args.data))] = [f.bbox for f in faces]
            face = largest(faces)
            if face is None:
                if is_known and idx >= args.enroll:
                    probes.append((label_of[name], None, True))  # counts as a false rejection
                continue
            detected += 1
            t0 = time.perf_counter()
            emb = backend.embed(img, face)
            emb_times.append(time.perf_counter() - t0)
            if is_known and idx < args.enroll:
                gallery_vecs.append(emb)
                gallery_labels.append(label_of[name])
            else:
                probes.append((label_of[name], emb, is_known))

    gallery = Gallery(np.stack(gallery_vecs), gallery_labels)
    enrolled = set(gallery_labels)

    def evaluate(threshold: float) -> dict:
        c = defaultdict(int)
        for true, emb, known in probes:
            known = known and true in enrolled
            if emb is None:
                c["known_rejected" if known else "unknown_rejected"] += 1
                continue
            m = gallery.match(emb, threshold, args.margin)
            if known:
                if m.student_id is None:
                    c["known_rejected"] += 1
                elif m.student_id == true:
                    c["correct"] += 1
                else:
                    c["misidentified"] += 1
            else:
                c["unknown_accepted" if m.student_id is not None else "unknown_rejected"] += 1
        n_known = c["correct"] + c["known_rejected"] + c["misidentified"]
        n_unknown = c["unknown_accepted"] + c["unknown_rejected"]
        return {
            "threshold": round(threshold, 3),
            "identification_rate": c["correct"] / n_known if n_known else None,
            "FRR": c["known_rejected"] / n_known if n_known else None,
            "misidentification_rate": c["misidentified"] / n_known if n_known else None,
            "FAR_unknown": c["unknown_accepted"] / n_unknown if n_unknown else None,
            # a false acceptance = attendance marked for the wrong person (unknown or misidentified)
            "FAR": (c["unknown_accepted"] + c["misidentified"]) / (n_known + n_unknown) if (n_known + n_unknown) else None,
            "counts": dict(c),
        }

    # closed-set rank-1 + confusion matrix
    confusion = np.zeros((len(names), len(names)), dtype=int)
    rank1 = n_rank = 0
    for true, emb, known in probes:
        if emb is None or not known or true not in enrolled:
            continue
        scores = gallery.per_student_scores(emb)
        pred = max(scores, key=scores.get)
        confusion[true, pred] += 1
        rank1 += pred == true
        n_rank += 1

    sweep = [evaluate(t) for t in np.round(np.arange(0.15, 0.71, 0.025), 3)]
    eer = min(sweep, key=lambda r: abs((r["FRR"] or 0) - (r["FAR"] or 0)))
    at_threshold = evaluate(args.threshold)

    ann = None
    if args.annotations:
        gt = defaultdict(list)
        with args.annotations.open() as f:
            for row in csv.DictReader(f):
                gt[row["file"]].append(tuple(int(float(row[k])) for k in ("x", "y", "w", "h")))
        tp = fp = fn = 0
        for fname, gts in gt.items():
            preds = list(det_boxes.get(fname, []))
            matched = set()
            for g in gts:
                best = max(range(len(preds)), key=lambda i: iou(g, preds[i]), default=None)
                if best is not None and best not in matched and iou(g, preds[best]) >= 0.5:
                    matched.add(best)
                    tp += 1
                else:
                    fn += 1
            fp += len(preds) - len(matched)
        ann = {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
               "tp": tp, "fp": fp, "fn": fn}

    det_ms, emb_ms = 1000 * np.mean(det_times), 1000 * np.mean(emb_times) if emb_times else 0.0
    report = {
        "dataset": str(args.data),
        "identities": len(names), "enrolled_identities": len(enrolled), "unknown_identities": sorted(unknown),
        "images": total_imgs, "probes": len(probes),
        "detection": {"detection_rate": detected / total_imgs if total_imgs else None,
                      "mean_faces_per_image": float(np.mean(faces_per_img)), "annotated": ann},
        "recognition": {"rank1_accuracy": rank1 / n_rank if n_rank else None, "at_threshold": at_threshold,
                        "eer_point": eer, "sweep": sweep},
        "performance": {"detection_ms": round(det_ms, 2), "embedding_ms": round(emb_ms, 2),
                        "est_fps_single_face": round(1000 / (det_ms + emb_ms), 1) if det_ms + emb_ms else None},
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    with (args.out / "confusion_matrix.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([""] + names)
        for i, n in enumerate(names):
            w.writerow([n] + confusion[i].tolist())
    with (args.out / "threshold_sweep.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["threshold", "identification_rate", "FRR", "FAR", "FAR_unknown", "misidentification_rate"])
        for r in sweep:
            w.writerow([r["threshold"], r["identification_rate"], r["FRR"], r["FAR"], r["FAR_unknown"], r["misidentification_rate"]])
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        th = [r["threshold"] for r in sweep]
        plt.figure(figsize=(6, 4))
        plt.plot(th, [r["FAR"] for r in sweep], label="FAR")
        plt.plot(th, [r["FRR"] for r in sweep], label="FRR")
        plt.axvline(args.threshold, ls="--", c="gray", label=f"threshold {args.threshold}")
        plt.xlabel("cosine similarity threshold")
        plt.ylabel("rate")
        plt.legend()
        plt.tight_layout()
        plt.savefig(args.out / "far_frr.png", dpi=150)
    except ImportError:
        pass

    a = at_threshold
    fmt = lambda v: "n/a" if v is None else f"{100 * v:.2f}%"  # noqa: E731
    print(f"Images: {total_imgs}  identities: {len(names)} ({len(unknown)} unknown)  probes: {len(probes)}")
    print(f"Detection rate:          {fmt(report['detection']['detection_rate'])}")
    if ann:
        print(f"Detection P / R @0.5IoU: {fmt(ann['precision'])} / {fmt(ann['recall'])}")
    print(f"Rank-1 accuracy:         {fmt(report['recognition']['rank1_accuracy'])}")
    print(f"@ threshold {args.threshold}: identification {fmt(a['identification_rate'])}, FRR {fmt(a['FRR'])}, "
          f"FAR {fmt(a['FAR'])} (unknown accepted {fmt(a['FAR_unknown'])})")
    print(f"EER ~ {fmt(eer['FAR'])} at threshold {eer['threshold']}")
    print(f"Latency: detection {det_ms:.1f} ms, embedding {emb_ms:.1f} ms per face")
    print(f"Results written to {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
