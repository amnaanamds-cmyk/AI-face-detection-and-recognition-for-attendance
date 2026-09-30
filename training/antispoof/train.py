"""Train the anti-spoofing model on crops made by prepare.py, then export it for the product.

    python training/antispoof/train.py --data data --epochs 25 --out runs/v1
    cp runs/v1/antispoof.onnx runs/v1/antispoof.json models/     # the product picks it up on restart

Recipe (same as the original MiniFASNet training): SGD, lr 0.1 with momentum 0.9, step decay,
loss = 0.5 * cross-entropy + 0.5 * MSE of the predicted Fourier spectrum. Classes are balanced
by sampling. After every epoch the model is scored on data/val with the standard ISO/IEC 30107-3
presentation-attack metrics and the best epoch (lowest ACER) is kept.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

from metrics import pad_metrics
from model import MultiFTNet

EXTS = {".png", ".jpg", ".jpeg"}


def class_names(root: Path) -> list[str]:
    """'live' is always class 0 (the product reads live_index from antispoof.json anyway)."""
    names = sorted(d.name for d in (root / "train").iterdir() if d.is_dir())
    if "live" not in names:
        raise SystemExit(f"{root}/train/live is missing")
    return ["live"] + [n for n in names if n != "live"]


def spectrum(img_bgr: np.ndarray, size: int) -> np.ndarray:
    """Normalised log-magnitude Fourier spectrum (target of the auxiliary branch)."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    mag = np.log(np.abs(np.fft.fftshift(np.fft.fft2(gray))) + 1)
    mag = (mag - mag.min() + 1) / (mag.max() - mag.min() + 1)
    return cv2.resize(mag, (size, size)).astype(np.float32)


def augment(img: np.ndarray, rng: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    # random crop 90-100 % + small rotation, resized back
    s = rng.uniform(0.9, 1.0)
    cw, ch = int(w * s), int(h * s)
    x0, y0 = rng.randint(0, w - cw), rng.randint(0, h - ch)
    m = cv2.getRotationMatrix2D((x0 + cw / 2, y0 + ch / 2), rng.uniform(-10, 10), 1.0)
    img = cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REFLECT)[y0:y0 + ch, x0:x0 + cw]
    img = cv2.resize(img, (w, h))
    if rng.random() < 0.5:
        img = cv2.flip(img, 1)
    # brightness / contrast / saturation jitter (lighting differs between sites)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= rng.uniform(0.6, 1.4)
    hsv[..., 2] = hsv[..., 2] * rng.uniform(0.6, 1.4) + rng.uniform(-20, 20)
    img = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    if rng.random() < 0.2:  # webcam compression
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(40, 90)])
        img = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return img


class CropDataset(Dataset):
    def __init__(self, root: Path, classes: list[str], size: int, train: bool):
        self.items = [(p, i) for i, c in enumerate(classes) if (root / c).exists()
                      for p in sorted((root / c).rglob("*")) if p.suffix.lower() in EXTS]
        self.size, self.train = size, train
        self.ft_size = MultiFTNet.spectrum_size(size)
        self.rng = random.Random()

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, label = self.items[i]
        img = cv2.imread(str(path))
        if img.shape[:2] != (self.size, self.size):
            img = cv2.resize(img, (self.size, self.size))
        if self.train:
            img = augment(img, self.rng)
        ft = spectrum(img, self.ft_size)
        # same input format as the product: RGB, [0, 1]
        x = torch.from_numpy(cv2.cvtColor(img, cv2.COLOR_BGR2RGB).transpose(2, 0, 1).copy()).float() / 255.0
        return x, torch.from_numpy(ft)[None], label


@torch.no_grad()
def live_scores(model: nn.Module, loader: DataLoader, device) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    scores, labels = [], []
    for x, _, y in loader:
        p = torch.softmax(model(x.to(device)), 1)[:, 0]
        scores.append(p.cpu().numpy())
        labels.append(y.numpy())
    return np.concatenate(scores), np.concatenate(labels)


def export_onnx(net: MultiFTNet, size: int, crop_scale: float, classes: list[str], out: Path) -> Path:
    classifier = net.model.eval().cpu()
    onnx_path = out / "antispoof.onnx"
    torch.onnx.export(classifier, torch.zeros(1, 3, size, size), str(onnx_path), input_names=["input"],
                      output_names=["logits"], opset_version=11, dynamo=False)
    meta = {"input_size": size, "crop_scale": crop_scale, "live_index": 0, "classes": classes, "rgb": True,
            "architecture": "MiniFASNetV2-SE (Apache-2.0, Minivision)", "created": time.strftime("%Y-%m-%d")}
    (out / "antispoof.json").write_text(json.dumps(meta, indent=2))
    return onnx_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True, help="folder made by prepare.py")
    ap.add_argument("--out", type=Path, default=Path("runs/antispoof"))
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--milestones", type=int, nargs="*", default=[10, 15, 22])
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=0.1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--threshold", type=float, default=0.7,
                    help="live-probability threshold used by the product (ANTISPOOF_THRESHOLD)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    prep = json.loads((args.data / "prepare.json").read_text()) if (args.data / "prepare.json").exists() else {}
    size, crop_scale = int(prep.get("input_size", 128)), float(prep.get("crop_scale", 1.5))
    classes = class_names(args.data)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    train_ds = CropDataset(args.data / "train", classes, size, train=True)
    val_ds = CropDataset(args.data / "val", classes, size, train=False)
    if not train_ds.items or not val_ds.items:
        raise SystemExit("train and val must both contain images - run prepare.py first")
    counts = np.bincount([c for _, c in train_ds.items], minlength=len(classes))
    print(f"classes {classes}  train {counts.tolist()}  val {len(val_ds)}  input {size}px  device {device}")
    weights = [1.0 / counts[c] for _, c in train_ds.items]
    train_dl = DataLoader(train_ds, batch_size=args.batch, num_workers=args.workers, drop_last=len(train_ds) > args.batch,
                          sampler=WeightedRandomSampler(weights, len(weights), replacement=True))
    val_dl = DataLoader(val_ds, batch_size=args.batch, num_workers=args.workers)

    net = MultiFTNet(num_classes=len(classes), input_size=size).to(device)
    opt = torch.optim.SGD(net.parameters(), lr=args.lr, momentum=0.9, weight_decay=5e-4)
    sched = torch.optim.lr_scheduler.MultiStepLR(opt, args.milestones, gamma=0.1)
    ce, mse = nn.CrossEntropyLoss(), nn.MSELoss()
    args.out.mkdir(parents=True, exist_ok=True)
    best, history = None, []
    for epoch in range(1, args.epochs + 1):
        net.train()
        t0, tot, n = time.time(), 0.0, 0
        for x, ft, y in train_dl:
            x, ft, y = x.to(device), ft.to(device), y.to(device)
            cls, ft_pred = net(x)
            loss = 0.5 * ce(cls, y) + 0.5 * mse(ft_pred, ft)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot, n = tot + loss.item() * len(y), n + len(y)
        sched.step()
        scores, labels = live_scores(net, val_dl, device)
        m = pad_metrics(scores, labels == 0, args.threshold)
        history.append({"epoch": epoch, "loss": tot / n, **m})
        print(f"epoch {epoch:3d}  loss {tot / n:.4f}  APCER {m['apcer']:.3f}  BPCER {m['bpcer']:.3f}  "
              f"ACER {m['acer']:.3f}  EER {m['eer']:.3f}  ({time.time() - t0:.0f}s)")
        if best is None or m["acer"] < best["acer"]:
            best = {"epoch": epoch, **m}
            torch.save({"state_dict": net.state_dict(), "classes": classes, "input_size": size,
                        "crop_scale": crop_scale}, args.out / "best.pt")
    (args.out / "history.json").write_text(json.dumps({"best": best, "epochs": history}, indent=2))
    ckpt = torch.load(args.out / "best.pt", map_location="cpu")
    net = MultiFTNet(num_classes=len(classes), input_size=size)
    net.load_state_dict(ckpt["state_dict"])
    path = export_onnx(net, size, crop_scale, classes, args.out)
    print(f"best epoch {best['epoch']} (ACER {best['acer']:.3f} at threshold {args.threshold}); exported {path}")
    print(f"install: copy {path.name} and antispoof.json into the product's models/ folder and restart")


if __name__ == "__main__":
    main()
