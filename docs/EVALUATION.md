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

## 2. Results on real face data

**Data.** The labelled test set of the open-source *deepface* project
(github.com/serengil/deepface, `tests/unit/dataset`): 61 real photographs of 13 people, with 520
unique labelled pairs (140 same-person, 380 different-person). The photos vary in lighting, pose,
age, make-up, glasses and resolution (from 0.3 to 12 megapixels). Real attack samples: the print
and screen-replay images published by Silent-Face-Anti-Spoofing
(github.com/minivision-ai/Silent-Face-Anti-Spoofing, `images/sample`).
Hardware: cloud CPU, no GPU.

### 2.1 Problems the real data revealed, and the fixes

| Finding on real photos | Fix |
|---|---|
| **28 % of photos had no face detected.** YuNet's confidence drops to 0.53–0.83 on faces wider than ~450 px (phone photos, a student close to the webcam). The same photos downscaled scored 0.93–0.96. | Multi-scale detection (640 px + full resolution up to 1920 px, merged with NMS). Now **100 %** detected. |
| At the model authors' threshold 0.363, **25 % of strangers** were matched to an enrolled person in 1:N identification (the highest different-person similarity was 0.431). | Default threshold raised to **0.45**. The genuine minimum was 0.504, so 0.45 sits inside the safe band 0.45–0.525. |
| A different student sitting down in the same place within 3 s would inherit the previous student's face track, and would never be marked. | Tracks are split when the embedding changes identity (similarity < 0.3). |
| Geometric liveness alone could be fooled by aggressive photo waving (§3.3) and cannot stop video replays. | Anti-spoofing CNN added (MiniFASNet, CelebA-Spoof, live / print / replay). |

### 2.2 Recognition (after the fixes)

`python scripts/evaluate.py --data <images> --pairs pairs.csv` (1:1 verification):

| Metric | Value |
|---|---|
| Pairs with an undetected face | 0 / 520 |
| Accuracy at threshold 0.363 | 99.81 % |
| TAR / FAR at 0.363 | 100 % / 0.26 % |
| ROC AUC | 1.000 |
| TAR @ FAR = 0.1 % | 100 % |
| Mean similarity: same person / different people | 0.738 / 0.136 |

`python scripts/evaluate.py --data <folder per person> --enroll 2 --unknown-fraction 0.25` (1:N identification, 10 enrolled + 3 strangers):

| Threshold | Identification rate | FRR | Strangers accepted | Misidentified |
|---|---|---|---|---|
| 0.363 | 100 % | 0 % | 25 % | 0 % |
| 0.40 | 100 % | 0 % | 8.3 % | 0 % |
| **0.45 (default)** | **100 %** | **0 %** | **0 %** | **0 %** |
| 0.525 | 100 % | 0 % | 0 % | 0 % |
| 0.55 | 96.6 % | 3.4 % | 0 % | 0 % |

Rank-1 (closed set): 100 %.

### 2.3 Anti-spoofing CNN

| Input | P(live) | Decision |
|---|---|---|
| 64 genuine photos | median 1.00, minimum 0.935 | 64 / 64 live |
| Real person, webcam (Silent-Face sample T1) | 0.999 | live |
| Printed photo attack (sample F1) | 0.015 | spoof |
| Screen replay attack (sample F2) | 0.001 | spoof |

Inference takes about 5 ms per face on a CPU through OpenCV DNN (no extra dependency). The model
authors report 93.3 % accuracy and 0.990 ROC AUC on the CelebA-Spoof test set. Two attack samples
confirm that the integration is correct, but they are not a measurement of its attack detection rate.
Measure that with `scripts/evaluate_liveness.py` on prints and phone screens of your own students.

### 2.4 End-to-end system test

`tests/test_real_data.py` drives the whole system through its web API, exactly as the browser does:
1. Import a CSV class list of 10 people and a ZIP of 2 photos each.
2. Start a session with liveness on (CNN mode).
3. Stream every remaining photo as 6 jittered, noisy 1280×720 webcam frames.
4. Close the session and export the report.

| Outcome | Count |
|---|---|
| Registered student marked with the **correct** name | **29 / 30** |
| Registered student marked with a **wrong** name | **0** |
| Still "checking liveness" after 6 frames (CNN P(live) 0.49, not rejected) | 1 |
| Stranger photos marked present | **0 / 11** |
| Real print / replay attacks rejected | **2 / 2** |
| Session closed: absentees auto-marked, Excel report exported | ✓ |

### 2.5 Speed

With 7 faces in view on a CPU, a 720p frame takes 258 ms end to end (multi-scale detection 72 ms,
about 27 ms per face for embedding and anti-spoofing). A 1080p frame takes 363 ms. Both fit in the
500 ms budget of the live page (2 frames/s).

## 3. Development measurements (sanity checks)

Hardware: cloud container CPU, OpenCV 4.x/5.x DNN, no GPU. Images: the public test images
`astronaut` (scikit-image) and `lena` (OpenCV samples), 512×512.

### 3.1 Detection and recognition

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

### 3.2 Liveness: why the design looks the way it does

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

### 3.3 Liveness: simulated performance with the final configuration

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

These figures are for the **head-motion cue on its own** (`LIVENESS_MODE=motion`). In the default
`auto`/`cnn` mode, the anti-spoofing CNN (§2.3) judges the texture of every frame instead, and
`cnn+motion` requires both checks to pass.

**Interpretation.** A photo held still or moved naturally is reliably rejected. An attacker who
deliberately waves and twists the photo gets through in a minority of attempts. The cue cannot stop a
**video replay** of the student turning their head, or a 3-D mask, because those contain real 3-D
motion. In a classroom the teacher is present and every spoof decision is written to the audit log,
which reduces the practical risk, but this limitation must be stated in the report. The natural
extension is a CNN-based anti-spoofing model (texture and moiré cues against screens and prints),
combined with the geometric cue.

## 4. Automated tests

`pytest -q` runs 41 tests without models or a camera. They cover the geometric invariance proof
(random affine motion of a flat face leaves (a, b) unchanged, while 3-D rotation changes it), liveness
decisions, matching, tracking and voting, attendance rules, database constraints, analytics, reports,
encryption, access control and the full web flow.
