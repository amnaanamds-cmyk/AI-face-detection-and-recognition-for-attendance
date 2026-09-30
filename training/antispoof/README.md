# Training your own anti-spoofing model

The product checks for photo and screen attacks in two ways. One is a head-movement test that
needs no model. The other is an **anti-spoofing CNN**, which is faster and harder to fool. The
free research CNN (`--include-research-antispoof`) was trained on CelebA-Spoof, which is
**non-commercial only**, so the commercial product cannot ship it. This folder trains your own
CNN on data you have the rights to.

* Architecture: MiniFASNetV2-SE with the auxiliary Fourier-spectrum branch, from
  [Silent-Face-Anti-Spoofing](https://github.com/minivision-ai/Silent-Face-Anti-Spoofing)
  (Apache-2.0, see `LICENSE-Silent-Face-Anti-Spoofing`; keep that file and the notice in
  `model.py` when you distribute). It has about 0.4 M parameters and runs in about 1 ms per face on a CPU.
* No pretrained weights are used. The training data decides what the model can do, and who owns it.

## 1. Data: what to collect

You need **commercially usable** data. Academic sets such as CelebA-Spoof, OULU-NPU,
CASIA-SURF, SiW and Replay-Attack do not qualify. You have two options: record your own with
written consent (recommended; it also matches your real cameras), or buy a licensed
presentation-attack dataset from a data vendor.

A good first dataset:

| | Minimum | Better |
|---|---|---|
| People | 50 | 200+ (varied age, skin tone, glasses, beards, headscarves) |
| Live clips per person | 2 × 10 s | several rooms, day and night light, 2–3 webcams and phones |
| Print attacks | matte + glossy A4 photos, 2 printers | also cut-out eyes, curved or bent paper |
| Replay attacks | phone + laptop screen | tablets, 4K monitors, different brightness levels |

Record at the distances and angles your kiosks and classrooms really use. Short videos work
well: the scripts take every 5th frame.

```
raw/
  live/    person_001/ clip1.mp4 clip2.mp4 ...
  print/   person_001/ ...      <- same folder name for the same person
  replay/  person_001/ ...
```

The train/validation split is made **per person folder**, so a person's frames never appear on
both sides. Validation scores are only honest when this holds.

## 2. Install (training machine only)

```bash
pip install -r requirements.txt                       # the product (detector code is reused)
pip install -r training/antispoof/requirements-train.txt
python scripts/download_models.py                     # YuNet detector, used to crop faces
```

A GPU helps a lot (about 25 epochs on 100k crops takes around an hour on a mid-range GPU). A CPU
is fine for small sets.

## 3. Prepare, train, evaluate

```bash
# crop faces exactly as the product will (size 128, 1.5x the face box)
python training/antispoof/prepare.py --raw raw --out data

# train (defaults = original recipe: SGD lr 0.1, 25 epochs, decay at 10/15/22)
python training/antispoof/train.py --data data --out runs/v1

# test through the product's own pipeline on recordings NOT used for training
python training/antispoof/evaluate.py --raw test_raw --models runs/v1
```

After every epoch, `train.py` prints the standard ISO/IEC 30107-3 metrics:

* **APCER**: share of attacks accepted as live. This is your security level.
* **BPCER**: share of real people rejected. They have to try again.
* **ACER**: the average of the two. **EER**: the error where both are equal.

It keeps the epoch with the lowest ACER and writes `runs/v1/antispoof.onnx` and
`runs/v1/antispoof.json`. The json holds the input size, crop scale and class order, so the
product always feeds the model the way it was trained.

## 4. Install in the product

```bash
cp runs/v1/antispoof.onnx runs/v1/antispoof.json models/
# restart the server
```

`models/antispoof.onnx` takes priority over the research model. With liveness mode `auto` (the
default) or `cnn`, check-ins now use your model. To choose the threshold, look at
`evaluate.py`'s output and set `ANTISPOOF_THRESHOLD` (default 0.7). Raise it for fewer accepted
attacks, lower it for fewer retries. For high-security sites, use liveness mode `cnn+motion`.

## Tested

The pipeline was run end to end (prepare → train → ONNX export → product inference) on a small
synthetic set. PyTorch and OpenCV outputs of the exported model agree to within 1e-5. That test
only proves the tooling works. **It says nothing about accuracy on real attacks**, which depends
entirely on the data you collect. Before you advertise anti-spoofing, measure APCER/BPCER on an
independent test set recorded in a different room, with different people and different attack
devices.
