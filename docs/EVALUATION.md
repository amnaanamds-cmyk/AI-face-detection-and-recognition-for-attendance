# Evaluation

The final report should contain **measurements from your own experiments**, not promised accuracy.
This document describes the protocol, the scripts that compute each metric, and the sanity-check
measurements taken while developing the system. Those checks used a handful of public sample images,
so they are **not** a substitute for a proper evaluation with real students.

## 1. Protocol for the FYP experiments

### 1.1 Recognition dataset

Collect with consent, for example 30–100 students, 8–10 images each, under several conditions, and
keep one folder per condition:

```
dataset_normal/    BSCS-2023-001/*.jpg ...   (good lighting, frontal)
dataset_lowlight/  ...                        (lights dimmed / evening)
dataset_angles/    ...                        (left, right, slightly tilted, looking down)
dataset_distance/  ...                        (front row vs. back row – face size in pixels)
```

```bash
python scripts/evaluate.py --data dataset_normal --out results/normal --enroll 3 --unknown-fraction 0.2
```

* The first `--enroll` images of each person form the gallery, the rest are probes.
* 20 % of identities are never enrolled. Their images test **unknown-person rejection**.
* If you annotate face boxes (`file,x,y,w,h` CSV), `--annotations` adds detection **precision/recall @ IoU 0.5**.
  Without annotations, only the detection rate is reported.

| Metric | Definition |
|---|---|
| Detection rate | images in which ≥ 1 face was found |
| Precision / Recall | detected boxes matching ground truth (IoU ≥ 0.5) |
| Rank-1 accuracy | closed set: nearest registered student is the correct one |
| Identification rate | open set at the threshold: probe accepted **and** correct |
| FRR (false rejection rate) | registered student rejected (not detected, below threshold or ambiguous) |
| FAR (false acceptance rate) | attendance would go to the wrong person (unknown accepted or misidentified) / all probes |
| EER | threshold where FAR ≈ FRR (from `threshold_sweep.csv`) |
| Confusion matrix | `confusion_matrix.csv` |
| Latency / FPS | mean detection and embedding time per face on your hardware |

Result table to fill in:

| Condition | Images | Detection rate | Rank-1 | Ident. rate @0.363 | FRR | FAR | EER | ms/face |
|---|---|---|---|---|---|---|---|---|
| Normal light | | | | | | | | |
| Low light | | | | | | | | |
| Angles | | | | | | | | |
| Distance | | | | | | | | |

### 1.2 Attendance-level test (end to end)

Run real sessions (e.g. 5 lectures) with the live page and a manual roll call as ground truth:

| Metric | How |
|---|---|
| Correctly marked | system record = manual roll call |
| Incorrect attendance | marked, but student absent (or wrong student) |
| Missed recognition | present but not marked by the camera |
| Duplicates | should be 0 (enforced by the database constraint); verify in the session table |
| Time to mark | `marked_at` minus the time the student entered the camera view |

### 1.3 Liveness

Record short clips (5–10 s) at the classroom camera position:

* `liveness_data/real/`: students looking at the camera and turning their head left and right.
* `liveness_data/spoof/`: printed photos (held still, moved, tilted), phone and laptop screens showing a
  photo, and phone screens showing a **video** of the student.

```bash
python scripts/evaluate_liveness.py --data liveness_data
```

This reports **APCER** (attacks accepted), **BPCER** (real people rejected) and **ACER** (their average),
following ISO/IEC 30107-3. Report the results separately for each attack type.

## 2. Development measurements (sanity checks)

Hardware: cloud container CPU, OpenCV 4.x/5.x DNN, no GPU. Images: the public test images
`astronaut` (scikit-image) and `lena` (OpenCV samples), 512×512.

### 2.1 Detection and recognition

| Check | Result |
|---|---|
| Faces detected (YuNet, score ≥ 0.85) | astronaut ✓ (0.93), lena ✓ (0.91) |
| Same person, brightness +20 % / +25 | cosine similarity 0.84 |
| Same person, downscaled 50 % | 0.96 |
| Same person, Gaussian blur 5×5 | 0.98 |
| Same person, rotated 12° | 0.94 |
| Different people (astronaut vs lena) | −0.10 |
| Detection latency (512×512) | ≈ 17 ms |
| Embedding latency (per face) | ≈ 21 ms |

All same-person pairs are far above the 0.363 threshold and the different-person pair is far below it.
These numbers are consistent with the model's intended operating range, but two identities are
**not** an accuracy estimate. Use §1.1 for that.

With the 2 frames/s used by the live page, a single CPU core can handle roughly 10 faces per frame
(≈ 40 ms per additional face).

### 2.2 Liveness: why the design looks the way it does

**Idea.** For a flat photo, the nose coordinates (a, b) in the affine frame (eye, eye, mouth centre)
are invariant to any affine motion of the photo. For a real head they change with yaw and pitch. With
a 3-D face model (nose ≈ 2 cm in front of the eye plane), a yaw of 10° / 20° / 30° changes `a` by
0.056 / 0.116 / 0.184. Pitch 10° changes `b` by 0.071.

**Measured problem.** The YuNet landmarks are learned, so they are not perfectly equivariant. When the
same photo was moved, the spread (10th–90th percentile) of `a` was:

| Photo motion | raw landmarks | after canonical re-detection |
|---|---|---|
| sensor noise only | 0.007 – 0.028 | 0.010 – 0.026 |
| in-plane rotation ±15°, scale 0.7–1.2, shift | 0.07 – 0.20 | 0.063 – 0.065 |
| in-plane rotation ±30° | 0.09 – 0.33 | 0.07 – 0.08 |
| out-of-plane tilt of the photo up to ±30° | 0.02 – 0.04 | – |
| strong perspective (corners ±40 px) | 0.05 – 0.11 | 0.05 – 0.06 |

The error comes mainly from **in-plane rotation and scale**, not from tilting the paper. The following
countermeasures were added, and each is covered by unit tests:

1. **Canonical re-detection:** before the landmarks are measured, each face is warped so that the eyes
   sit at fixed positions (removing rotation and scale), and the landmarks are detected again.
2. **Temporal median over 3 frames:** removes single-frame jitter.
3. **Robust spread** (P90 − P10) instead of max − min.
4. **Roll gate:** frames rotated more than 15° in-plane are ignored. A seated student rarely does this;
   a waved photo often does.
5. **Threshold 0.12** (≈ a 20° head turn), with at least 6 frames and a 12 s timeout.
6. **Sharpness check:** faces whose median Laplacian variance is very low (heavily blurred replays) are rejected.

### 2.3 Liveness: simulated performance with the final configuration

**Genuine users (model-based simulation).** A 3-D head turning sinusoidally, with random pitch
jitter and landmark noise calibrated to the measured YuNet jitter (std of `a` ≈ 0.010). 200 trials
per row. Default settings, 2 frames/s.

| Head turn amplitude | Accepted as live | Mean time to accept |
|---|---|---|
| ±5° | 0 % | – |
| ±10° | 5 % | 5.0 s |
| ±15° | 95.5 % | 3.3 s |
| ±20° | 100 % | 2.6 s |
| ±30° | 100 % | 2.5 s |

As a result, the live page tells students to "turn your head left and right (about 20°)".

**Photo attacks (image-based simulation, `scripts/simulate_photo_attack.py`).** A real face photo is
moved along smooth random hand trajectories with rotation, scale, shift, paper tilt and sensor noise.
Each attack is run through the real detector and checker. 20 trials × 2 photos per row.

| Attack style | In-plane rotation | Paper tilt (corner shift) | Accepted as live (APCER) |
|---|---|---|---|
| Gentle | ≤ 10° | ≤ 20 px | 0 / 40 = 0 % |
| Moderate | ≤ 20° | ≤ 40 px | 0 / 40 = 0 % |
| Aggressive | ≤ 35° | ≤ 60 px | 10 / 40 = 25 % |

Before the canonical re-detection and the roll gate were added, the moderate and aggressive attacks
were accepted in 10 % and 40 % of trials respectively.

**Interpretation.** A photo held still or moved naturally is reliably rejected. An attacker who
deliberately waves and twists the photo gets through in a minority of attempts. The cue cannot stop a
**video replay** of the student turning their head, or a 3-D mask, because those contain real 3-D
motion. In a classroom the teacher is present and every spoof decision is written to the audit log,
which reduces the practical risk, but this limitation must be stated in the report. The natural
extension is a CNN-based anti-spoofing model (texture and moiré cues against screens and prints),
combined with the geometric cue.

## 3. Automated tests

`pytest -q` runs 32 tests without models or a camera. They cover the geometric invariance proof
(random affine motion of a flat face leaves (a, b) unchanged, while 3-D rotation changes it), liveness
decisions, matching, tracking and voting, attendance rules, database constraints, analytics, reports,
encryption, access control and the full web flow.
