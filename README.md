# Smart Classroom Attendance System Using Face Recognition and Liveness Detection

A complete AI-based attendance management system: a camera looks at the classroom, the system
**detects every face**, **recognises registered students**, checks that each face is a **live person**
(not a photo or screen), applies **attendance rules** (Present / Late / Absent, no duplicates),
stores everything in a **relational database**, and provides a **dashboard, analytics, alerts and
Excel/PDF/CSV reports** for admins, teachers and students.

```
Camera ─► Face detection (YuNet) ─► Tracking ─► Alignment ─► Embedding (SFace, 128-D)
       ─► Similarity matching ─► Multi-frame voting ─► Liveness (3-D motion + sharpness)
       ─► Attendance rules (windows, enrollment, duplicates) ─► Database ─► Dashboard / Reports
```

| | |
|---|---|
| **AI / CV** | OpenCV DNN, YuNet face detector, SFace face recogniser (ArcFace-style margin loss), NumPy |
| **Backend** | Python 3.10+, FastAPI, SQLAlchemy 2 (SQLite by default, PostgreSQL-ready) |
| **Frontend** | Server-rendered Jinja2 + Bootstrap 5 + Chart.js (vendored – works offline), browser camera via `getUserMedia` |
| **Reports** | openpyxl (Excel), ReportLab (PDF), CSV |
| **Security** | PBKDF2 password hashing, role-based access, Fernet-encrypted face embeddings, no raw face photos stored |

---

## 1. Quick start

```bash
git clone <this repo> && cd AI-face-detection-and-recognition-for-attendance
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/download_models.py                       # YuNet + SFace (~38 MB) into models/
uvicorn app.main:app --reload                           # open http://127.0.0.1:8000
```

Log in with **admin / admin123** and change the password immediately (top-right **Password**).
Optional: `python scripts/seed_demo.py` fills the database with demo courses, 25 demo students and
six weeks of history so the dashboard, analytics and reports have something to show
(teacher login `teacher / teacher123`).

> The browser only allows camera access on `http://localhost` or over **HTTPS**. To use a
> classroom PC or phone as the camera, serve the app over HTTPS
> (e.g. `uvicorn app.main:app --host 0.0.0.0 --ssl-keyfile key.pem --ssl-certfile cert.pem`).
> Alternatively use the desktop mode: `python scripts/webcam_attendance.py --session <id>`
> (requires `pip install opencv-python` for the preview window).

## 2. Typical workflow

1. **Admin → Users**: create teacher accounts (e.g. *Dr. Ahmad*).
2. **Courses → Add course**: e.g. *CS-401 Artificial Intelligence*, semester 7, section A, teacher.
3. **Students → Register student**: ID `BSCS-2023-001`, name, department, semester, section,
   contact, **consent checkbox**; optionally auto-enrol in matching courses and create a student login.
4. **Register face**: capture 3–5 images with the webcam (or upload photos). Each image must contain
   exactly one face; the photos are converted to embeddings, encrypted and discarded.
5. **Attendance → Create & start**: pick the course, start time, Present/Late windows and whether
   liveness is required. The **live camera** page draws a box and label on every face:
   `Identifying… → Amina Bibi ✓ marked` / `Unknown` / `Spoof suspected` / `already marked`.
6. **Close session**: every enrolled student without a record becomes *Absent*; low-attendance alerts
   are generated (and e-mailed if SMTP is configured).
7. **Session page**: manual override (Excused, Leave, corrections). **Reports**: daily / monthly /
   per session → view, Excel, PDF, CSV. **Analytics**: trends, course-wise rates, best/worst, absences by weekday.
8. **Students** log in to see their own attendance percentage per course and any warnings.

## 3. Features ↔ project modules

| Module | What is implemented | Where |
|---|---|---|
| 1. User & student management | Students (ID, roll no., name, dept., semester, section, e-mail, phone, consent), courses, enrolments, users with roles | `app/routers/students.py`, `courses.py`, `admin.py` |
| 2. Face enrollment | Webcam capture / upload, one-face check, same-person consistency check, quality score, encrypted templates | `app/services/faces.py`, `templates/students/enroll.html` |
| 3. AI face recognition | YuNet multi-face detection, 5-point alignment, SFace 128-D embeddings, cosine matching with threshold + margin (unknown detection), IoU face tracking, 3-frame voting | `app/vision/` |
| 4. Attendance management | Sessions, configurable Present/Late/Absent windows, enrolment check, duplicate prevention (service check **and** `UNIQUE(student, session)`), auto-absent on close, manual Excused/Leave | `app/services/attendance.py` |
| 5. Liveness / anti-spoofing | Planar-vs-3-D test on landmark geometry over time, canonical re-detection, jitter filter, roll gate, blur check; audit log of spoof attempts | `app/vision/liveness.py` |
| 6. Analytics & reporting | Dashboard KPIs, daily/weekly/monthly trends, course-wise, ranking, weekday absences; daily/monthly/session reports to Excel/PDF/CSV; low-attendance alerts | `app/services/analytics.py`, `reports.py`, `notifications.py` |
| 7. Administration & security | Admin / Teacher / Student roles, configurable thresholds & rules, password management, AI audit log, biometric deletion | `app/routers/admin.py`, `app/security.py` |
| Evaluation | Detection rate / P-R, rank-1, identification rate, FAR, FRR, EER, threshold sweep, confusion matrix, latency/FPS; liveness APCER/BPCER/ACER | `scripts/evaluate.py`, `scripts/evaluate_liveness.py` |

### Roles

| Admin | Teacher | Student |
|---|---|---|
| everything: students, faces, courses, users, settings, all attendance & reports, audit log | own courses: start/close sessions, live attendance, overrides, analytics, reports, alerts | own attendance history, per-course percentage, warnings |

## 4. How the AI works (short version)

* **Detection** – YuNet (a light CNN, ~230 KB) finds all faces in a frame with a confidence score and
  5 landmarks (eyes, nose, mouth corners). Faces smaller than `MIN_FACE_SIZE` are ignored.
* **Alignment + embedding** – the 5 landmarks are mapped to a canonical 112×112 face; SFace turns it
  into a 128-dimensional, L2-normalised vector.
* **Matching** – cosine similarity against every registered template; the best student must exceed
  `MATCH_THRESHOLD` (default 0.363) **and** beat the next student by `MATCH_MARGIN`, otherwise the face
  is *Unknown*.
* **Voting** – faces are tracked between frames; a student is confirmed only after
  `VOTES_REQUIRED` (3) of the last 5 frames agree. This suppresses one-frame mistakes.
* **Liveness** – a photo/screen is flat, so the nose position expressed in the eye/mouth frame cannot
  change when it moves; a real head turning changes it. The student is asked to turn their head
  slightly; a flat face within the time window is flagged *Spoof suspected*. Details, measurements
  and limitations: [docs/EVALUATION.md](docs/EVALUATION.md).
* **Rules** – arrival within the Present window → *Present*, within the Late window → *Late*, later →
  *Absent* (recorded with a note). Each student gets at most one record per session.

Full architecture, database schema and sequence diagrams: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Privacy, security and ethics: [docs/PRIVACY_SECURITY.md](docs/PRIVACY_SECURITY.md).

## 5. Evaluating the system (for the FYP report)

```bash
# Recognition: dataset/<student_id>/*.jpg  (collect under different lighting / angles / distances)
python scripts/evaluate.py --data dataset_normal   --out results/normal
python scripts/evaluate.py --data dataset_lowlight --out results/lowlight

# Liveness: liveness_data/real/*.mp4 and liveness_data/spoof/*.mp4 (printed photos, phone replays)
python scripts/evaluate_liveness.py --data liveness_data
```

Outputs: `report.json`, `threshold_sweep.csv`, `confusion_matrix.csv`, `far_frr.png` (if matplotlib is
installed) and liveness APCER/BPCER/ACER. Report the numbers you actually measure – see
[docs/EVALUATION.md](docs/EVALUATION.md) for the protocol and result tables to fill in.

## 6. Tests

```bash
pytest -q
```

The test-suite (32 tests) runs without models or a camera by using a deterministic `FakeBackend`.
It covers liveness geometry (a simulated 3-D head vs. a flat photo), matching/unknown/ambiguous
decisions, tracking and voting, attendance windows, duplicate prevention (including the database
constraint), closing sessions, analytics, alerts, report exports, encryption, role-based access and
an end-to-end web flow (register → enrol faces → live frames → marked → duplicate → unknown → spoof → close → reports).

## 7. Project structure

```
app/
  main.py              FastAPI app, auth middleware, first-admin bootstrap
  config.py            settings (environment / .env)
  models.py            SQLAlchemy tables
  security.py          password hashing, embedding encryption
  deps.py              current user, role checks, template rendering
  vision/              AI: backends (YuNet+SFace), matcher, liveness, tracking pipeline
  services/            attendance rules, enrollment, analytics, reports, notifications, settings
  routers/             web pages + JSON API
  templates/, static/  UI (Bootstrap, Chart.js vendored), camera + live overlay JS
scripts/               download_models, evaluate, evaluate_liveness, webcam_attendance, seed_demo
tests/                 pytest suite
docs/                  architecture, evaluation, privacy & security
```

## 8. Configuration

Copy `.env.example` to `.env`. Most useful settings: `SECRET_KEY`, `DATABASE_URL`, `MATCH_THRESHOLD`,
`VOTES_REQUIRED`, `LIVENESS_*`, `SMTP_*`. Attendance windows, thresholds and the low-attendance limit can
also be changed at run time under **Admin → System settings**.

## 9. Known limitations / future work

* Liveness uses geometry from 5 landmarks. It reliably rejects static and gently moved photos but can be
  fooled by aggressively waved/twisted photos (25 % of simulated attempts) and by a **video replay** of the
  student turning their head (see measurements in `docs/EVALUATION.md`). A deep-learning anti-spoofing
  model (e.g. MiniFASNet) can be added behind the same `LivenessChecker` interface.
* Classroom-wide recognition needs faces of roughly ≥ 40 px; use a high-resolution camera near the front
  of the room or several cameras for large halls.
* Trackers live in memory per server process – run a single worker (default) or move them to Redis.
* The browser sends ~2 frames/s; a GPU or smaller input size is needed for very large classes.

## License / models

Code: add the license your department requires. YuNet and SFace are distributed by the OpenCV Model Zoo
(Apache-2.0 / MIT – see the model pages). Bootstrap, Bootstrap Icons and Chart.js are MIT-licensed
(see `app/static/vendor/*/LICENSE*`).
