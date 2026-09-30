"""Custom anti-spoofing model support (no torch needed): sidecar config, shared crop, PAD metrics."""
import json
import sys
from pathlib import Path

import numpy as np

from app.vision.backends import AntiSpoofConfig, crop_face

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "training" / "antispoof"))
from metrics import pad_metrics  # noqa: E402


def test_sidecar_config(tmp_path):
    assert AntiSpoofConfig.load(tmp_path / "missing.json") == AntiSpoofConfig()
    side = tmp_path / "antispoof.json"
    side.write_text(json.dumps({"input_size": 80, "crop_scale": 2.7, "live_index": 1, "rgb": False, "classes": ["a", "live"]}))
    assert AntiSpoofConfig.load(side) == AntiSpoofConfig(80, 2.7, 1, False)


def test_crop_face_is_centred_and_padded():
    img = np.zeros((100, 100, 3), np.uint8)
    img[40:60, 40:60] = 255
    crop = crop_face(img, (40, 40, 20, 20), 2.0, 40)
    assert crop.shape == (40, 40, 3)
    assert crop[20, 20].max() == 255 and crop[0, 0].max() == 0  # face in the middle, context around it
    edge = crop_face(img, (-10, -10, 20, 20), 1.5, 30)  # face partly outside the frame -> zero padding
    assert edge.shape == (30, 30, 3)


def test_pad_metrics():
    live = np.array([0.9, 0.8, 0.4, 0.95])
    attack = np.array([0.1, 0.75, 0.2, 0.05])
    m = pad_metrics(np.concatenate([live, attack]), np.array([1] * 4 + [0] * 4, bool), 0.7)
    assert m["apcer"] == 0.25 and m["bpcer"] == 0.25 and m["acer"] == 0.25
    assert 0 <= m["eer"] <= 0.25
