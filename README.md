# Smart Classroom Attendance System Using Face Recognition and Liveness Detection

A complete AI-based attendance management system: a camera looks at the classroom, the system
**detects every face**, **recognises registered students**, checks that each face is a **live person**
(not a photo or screen), applies **attendance rules** (Present / Late / Absent, no duplicates),
stores everything in a **relational database**, and provides a **dashboard, analytics, alerts and
Excel/PDF/CSV reports** for admins, teachers and students.

```
Camera ─► Face detection (YuNet) ─► Tracking ─► Alignment ─► Embedding (SFace, 128-D)
       ─► Similarity matching ─► Multi-frame voting ─► Liveness (anti-spoofing CNN / 3-D motion)
       ─► Attendance rules (windows, enrollment, duplicates) ─► Database ─► Dashboard / Reports
```

| | |
|---|---|
| **AI / CV** | OpenCV DNN: YuNet face detector (multi-scale), SFace face recogniser (hypersphere margin loss, ArcFace family), MiniFASNet anti-spoofing CNN, NumPy |
| **Backend** | Python 3.10+, FastAPI, SQLAlchemy 2 (SQLite by default, PostgreSQL-ready) |
| **Frontend** | Server-rendered Jinja2 + Bootstrap 5 + Chart.js (vendored – works offline), browser camera via `getUserMedia` |
| **Reports** | openpyxl (Excel), ReportLab (PDF), CSV |
| **Security** | PBKDF2 password hashing, role-based access, Fernet-encrypted face embeddings, no raw face photos stored |

### Product editions

The same code runs as a **hosted SaaS** (many customer organizations, sign-up, Stripe
subscriptions, public landing page) or as a **self-hosted** installation (licence keys), for
schools, offices, and gyms/events (the wording adapts: students/classes, employees/shifts,
members/events).

| Document | For |
|---|---|
| [docs/COMPETITIVE_FEATURES.md](docs/COMPETITIVE_FEATURES.md) | Ten differentiators (forecast, tamper-evident ledger, proxy watch, two-way parent SMS, Ask FaceAttend, revocable face data, self-learning recognition, emergency roll call, self-deleting visitor passes, exam guard) and a demo script |
| [docs/MOBILE_AND_MESSAGES.md](docs/MOBILE_AND_MESSAGES.md) | Phones (share online / same Wi-Fi), the Android app (Android Studio), SMS / WhatsApp messages to parents |
| [docs/SAAS_DEPLOYMENT.md](docs/SAAS_DEPLOYMENT.md) | Running the hosted edition: Docker + HTTPS + Stripe, and selling self-hosted licences |
| [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md) | Legal, security and quality checks before selling |
| [docs/PRODUCT_HUNT_LAUNCH.md](docs/PRODUCT_HUNT_LAUNCH.md) | Product Hunt listing, first comment, gallery images, launch plan |
| [training/antispoof/README.md](training/antispoof/README.md) | Training your own commercially usable anti-spoofing model |

---

## Download

| | |
|---|---|
| **Android app** | **[Download FaceAttend.apk](https://github.com/amnaanamds-cmyk/AI-face-detection-and-recognition-for-attendance/releases/latest/download/FaceAttend.apk)**, or scan the QR code with the phone camera |
| **Windows (computer that runs the system)** | **[Download FaceAttend-Setup.exe](https://github.com/amnaanamds-cmyk/AI-face-detection-and-recognition-for-attendance/releases/latest/download/FaceAttend-Setup.exe)** |
| All versions and release notes | [Releases](https://github.com/amnaanamds-cmyk/AI-face-detection-and-recognition-for-attendance/releases) |

<img src="docs/download-android-qr.png" width="180" alt="QR code: download the Android app">

**Install on Android:** open the downloaded `FaceAttend.apk` → if asked, allow *Install unknown apps* for your
browser → **Install** → open FaceAttend → **Scan QR code** shown on the computer (*Connect phones / share online*).
The app checks for new versions by itself (menu ⋮ → *Check for updates*) and updates keep all settings.
The computer running FaceAttend must be on for phones to work.

**Using it in a school** (teachers on phones, principal overview): see [docs/SCHOOL_SETUP.md](docs/SCHOOL_SETUP.md).

**One website for every school and college** (each signs up and gets its own account, pays by bank /
JazzCash / Easypaisa): [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/amnaanamds-cmyk/AI-face-detection-and-recognition-for-attendance)
then follow [docs/GO_LIVE.md](docs/GO_LIVE.md).

## 0. Desktop app (.exe) - easiest

**FaceAttend-Setup.exe** installs a normal Windows program: double-click the icon and the app
opens in its own window. The attendance server runs in the background (tray icon next to the
clock, optionally started with Windows), so closing the window does not break anything and every
shortcut, browser tab or phone keeps working. Quit from the tray icon. There is no console window
and no Python to install. Phones on the same Wi-Fi can connect while it runs (Mobile app page). Data is stored in
`%LOCALAPPDATA%\FaceAttend`.

* **Download:** GitHub → *Actions* → *Build apps* → latest run → *Artifacts*
  (`FaceAttend-Setup`, `FaceAttend-portable`, and `FaceAttend-android` for the phone app). Tag a version (`git tag v1.0.0 && git push --tags`)
  to publish it as a GitHub Release you can share with a link.
* **Build it yourself on Windows:** `pip install -r requirements.txt pyinstaller pillow` then
  `python packaging/build_exe.py` (add `--include-research-antispoof` for an academic build).
  Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) to also get the installer.
* **Keep data from the source version:** copy `data\attendance.db` and `data\.secret_key` into
  `%LOCALAPPDATA%\FaceAttend` before the first start of the .exe.

## 1. Quick start

**Windows:** double-click `start.bat`. **Linux / macOS:** `./start.sh`.
The first run creates a virtual environment, installs the requirements and downloads the three
AI models (~40 MB). After that, open **http://127.0.0.1:8000** and log in with
**admin / admin123**. Change this password immediately (top-right **Password**).

Manual equivalent:

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/download_models.py                       # YuNet + SFace + anti-spoofing CNN into models/
python run.py                                           # http://127.0.0.1:8000
```

## 1a. Mobile app (Android / iPhone) and web app

The same system is a **web app** on PCs and an **installable mobile app** (PWA) on phones and
tablets. It gets a home-screen icon, runs full-screen, works with the front and back camera, and
shows an offline screen when the server can't be reached. No app store is needed.

1. On the server PC, start it with **`start-mobile.bat`** (Windows) or **`./start.sh --lan`**.
   The window prints the phone address, e.g. `https://192.168.1.10:8443`. If Windows asks,
   **allow Python on private networks**.
2. On that PC, open **https://localhost:8443/mobile**. It shows a **QR code** for the phones and a
   certificate to install once per phone (step-by-step instructions for Android and iPhone are on the page).
3. On the phone (same Wi-Fi): scan the QR code, log in, then tap **Install app** (Android Chrome) or
   **Share → Add to Home Screen** (iPhone Safari).

- **Teachers** use the phone's back camera to take attendance.
- **Students** log in with their Student ID to see their attendance.

Optional: `python scripts/seed_demo.py` adds demo data so the dashboard has something to show.

## 2. Typical workflow

1. **Admin → Users**: create teacher accounts (e.g. *Dr. Ahmad*).
2. **Courses → Add course**: e.g. *CS-401 Artificial Intelligence*, semester 7, section A, teacher.
3. **Students → Bulk import**: upload your real class list (Excel/CSV: Student ID, Name, Roll No,
   Department, Semester, Section, Email, Phone, Consent) and a **ZIP of face photos** (one folder per
   Student ID, 3–5 photos each, phone photos are fine). Or register students one by one with
   **Register student**. CLI: `python scripts/import_data.py --students list.xlsx --faces photos/`.
4. **Register face** (per student): capture 3–5 images with the webcam, or upload photos. The student
   must be the dominant face. A face that is already registered to another student is refused.
   Photos are converted to encrypted templates and discarded.
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
| 1. User & student management | Students (ID, roll no., name, dept., semester, section, e-mail, phone, consent), **Excel/CSV import**, courses, enrolments, users with roles | `app/routers/students.py`, `courses.py`, `admin.py` |
| 2. Face enrollment | Webcam capture / upload / **bulk ZIP**, dominant-face selection, same-person consistency check, **duplicate-identity check**, quality score, encrypted templates | `app/services/faces.py`, `templates/students/enroll.html` |
| 3. AI face recognition | YuNet **multi-scale** multi-face detection, 5-point alignment, SFace 128-D embeddings, cosine matching with threshold + margin (unknown detection), IoU + identity-aware face tracking, 3-frame voting | `app/vision/` |
| 4. Attendance management | Sessions, configurable Present/Late/Absent windows, enrolment check, duplicate prevention (service check **and** `UNIQUE(student, session)`), auto-absent on close, manual Excused/Leave | `app/services/attendance.py` |
| 5. Liveness / anti-spoofing | **Anti-spoofing CNN** (print/replay) + planar-vs-3-D head-motion test (modes `cnn`, `motion`, `cnn+motion`), blur check; audit log of spoof attempts | `app/vision/liveness.py` |
| 6. Analytics & reporting | Dashboard KPIs, daily/weekly/monthly trends, course-wise, ranking, weekday absences; daily/monthly/session reports to Excel/PDF/CSV; low-attendance alerts | `app/services/analytics.py`, `reports.py`, `notifications.py` |
| 7. Administration & security | Admin / Teacher / Student roles, configurable thresholds & rules, password management, login lock-out, HTTPS launcher, backups, AI audit log, biometric deletion | `app/routers/admin.py`, `app/security.py` |
| Evaluation | Detection rate / P-R, rank-1, identification rate, FAR, FRR, EER, threshold sweep, confusion matrix, latency/FPS; liveness APCER/BPCER/ACER | `scripts/evaluate.py`, `scripts/evaluate_liveness.py` |

### Roles

| Admin | Teacher | Student |
|---|---|---|
| everything: students, faces, courses, users, settings, all attendance & reports, audit log | own courses: start/close sessions, live attendance, overrides, analytics, reports, alerts | own attendance history, per-course percentage, warnings |

## 4. How the AI works (short version)

* **Detection** – YuNet (a light CNN, ~230 KB) finds all faces in a frame with a confidence score and
  5 landmarks (eyes, nose, mouth corners). It runs at two scales: 640 px catches large or close-up
  faces, and full resolution (up to 1920 px) catches small faces at the back of the room. Faces
  smaller than `MIN_FACE_SIZE` are ignored.
* **Alignment + embedding** – the 5 landmarks are mapped to a canonical 112×112 face; SFace turns it
  into a 128-dimensional, L2-normalised vector.
* **Matching** – cosine similarity against every registered template; the best student must exceed
  `MATCH_THRESHOLD` (default 0.45, chosen from real-photo measurements) **and** beat the next student by `MATCH_MARGIN`, otherwise the face
  is *Unknown*.
* **Voting** – faces are tracked between frames; a student is confirmed only after
  `VOTES_REQUIRED` (3) of the last 5 frames agree. This suppresses one-frame mistakes.
* **Liveness** – default `auto` mode uses a **MiniFASNet anti-spoofing CNN**. It is trained on
  CelebA-Spoof to tell live faces from printed photos and screen replays, and it is passive: students
  just look at the camera. Without the model, or in `motion` / `cnn+motion` mode, the system also
  checks 3-D head movement. A flat photo cannot change the nose position relative to the eyes and
  mouth, while a real turning head does. Details and measurements: [docs/EVALUATION.md](docs/EVALUATION.md).
* **Rules** – arrival within the Present window → *Present*, within the Late window → *Late*, later →
  *Absent* (recorded with a note). Each student gets at most one record per session.

Full architecture, database schema and sequence diagrams: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Privacy, security and ethics: [docs/PRIVACY_SECURITY.md](docs/PRIVACY_SECURITY.md).

## 5. Results on real face data

Measured on 61 real photographs of 13 people (varied lighting, pose, age and resolution; the
labelled test set of the open-source *deepface* project), with the real models:

| Test | Result |
|---|---|
| Face detection | **100 %** of photos (72 % before multi-scale detection was added) |
| 1:1 verification, 520 labelled pairs | **99.8 % accuracy**, ROC AUC 1.000, 0 same-person pairs rejected |
| 1:N identification (10 enrolled, 3 strangers) at 0.45 | **100 %** identified, **0 %** strangers accepted, 0 misidentifications |
| End-to-end through the web API (import list + ZIP, live session, liveness on) | **29 / 30** visits marked with the correct name, **0** wrong names, **0 / 11** strangers marked |
| Real spoof samples (printed photo, screen replay) | **both rejected** (anti-spoofing CNN P(live) = 0.00) |
| Genuine photos accepted by the anti-spoofing CNN | 64 / 64 |
| Speed (CPU, 7 faces in frame) | 720p: 258 ms per frame, 1080p: 363 ms per frame (detection 72 / 152 ms + ~28 ms per face), within the 500 ms budget at 2 frames/s |

This is a small public benchmark. Before using the system for real grading, run the same scripts
on your own students (next section) and report those numbers. Full details are in
[docs/EVALUATION.md](docs/EVALUATION.md).

## 6. Evaluating on your own students

```bash
# Identification: dataset/<student_id>/*.jpg (different lighting / angles / distances)
python scripts/evaluate.py --data dataset_normal --out results/normal
# Verification on labelled pairs (file_x,file_y,same):
python scripts/evaluate.py --data images/ --pairs pairs.csv --out results/pairs
# Liveness: liveness_data/real/*.mp4 and liveness_data/spoof/*.mp4 (prints, phone replays)
python scripts/evaluate_liveness.py --data liveness_data
# Full system replay on your photos (import -> session -> frames -> marks), incl. spoof photos:
REAL_FACES_DIR=dataset_normal SPOOF_DIR=spoof_photos pytest tests/test_real_data.py -s
```

## 7. Tests

```bash
pytest -q
```

45 tests run without models or a camera, using a deterministic `FakeBackend`. They cover liveness (a
simulated 3-D head vs. a flat photo; CNN, motion and combined modes), matching, unknown and ambiguous
faces, tracking (including a different student taking the same seat), voting, attendance windows,
duplicate prevention (including the database constraint), closing sessions, Excel/CSV/ZIP import,
duplicate-identity rejection, analytics, alerts, report exports, encryption, login lock-out,
role-based access, the mobile app files (manifest, service worker, icons), the phone certificate
authority, and an end-to-end web flow. `tests/test_real_data.py` runs the whole system on real
photos when `REAL_FACES_DIR` is set.

## 8. Project structure

```
run.py, start.bat, start-mobile.bat, start.sh   launchers (HTTP locally, HTTPS for phones on the LAN)
Dockerfile, docker-compose.yml
app/
  main.py              FastAPI app, auth middleware, first-admin bootstrap
  config.py            settings (environment / .env)
  models.py            SQLAlchemy tables
  security.py          password hashing, embedding encryption
  deps.py              current user, role checks, template rendering
  vision/              AI: backends (YuNet, SFace, anti-spoofing CNN), matcher, liveness, tracking pipeline
  services/            attendance rules, enrollment, import, analytics, reports, notifications, settings
  routers/             web pages + JSON API
  templates/, static/  UI (Bootstrap, Chart.js vendored), camera + live overlay JS,
                       PWA manifest, service worker and app icons (mobile app)
scripts/               download_models, import_data, evaluate, evaluate_liveness, simulate_photo_attack,
                       webcam_attendance, backup, seed_demo
tests/                 pytest suite (+ optional real-data end-to-end test)
docs/                  deployment, architecture, evaluation, privacy & security
```

## 9. Configuration

Copy `.env.example` to `.env`. The most useful settings are `SECRET_KEY`, `DATABASE_URL`,
`MATCH_THRESHOLD`, `LIVENESS_MODE`, `VOTES_REQUIRED` and `SMTP_*`. Attendance windows, thresholds,
the liveness method and the low-attendance limit can also be changed at run time under
**Admin → System settings**. That page also shows which AI models are installed.

## 10. Known limitations

* The research anti-spoofing CNN was validated here on 64 genuine photos and 2 real attack samples. It is
  trained on CelebA-Spoof (non-commercial), and a 3-D silicone mask is outside its scope. For the
  commercial product, train your own model (`training/antispoof/`).
* A still picture injected through a virtual camera is rejected (frozen-feed check). Injected
  *video* cannot be detected by any browser-based check, so keep camera devices under the
  organization's control (kiosk tablets, staff laptops). Measure it on your own
  cameras with `scripts/evaluate_liveness.py`. For high-stakes use, choose `cnn+motion` mode (CNN
  **and** head turn).
* Faces need to be about 40 px or larger. For large halls, use 1080p (selector on the live page)
  and a camera near the front, or several cameras.
* Face trackers live in the server process, so run a single worker (the launchers do this).
* On a CPU, around 10–15 faces per frame at 2 frames/s is comfortable (measured above). Larger classes need a GPU build
  of OpenCV or several cameras/servers.

## License / models

Code: add the license your department requires. YuNet (MIT) and SFace (Apache-2.0) come from the OpenCV
Model Zoo. The research anti-spoofing model (only with `--include-research-antispoof`) is downloaded from
[hairymax/Face-AntiSpoofing](https://github.com/hairymax/Face-AntiSpoofing). It uses the MiniFASNet
architecture from the Apache-2.0 [Silent-Face-Anti-Spoofing](https://github.com/minivision-ai/Silent-Face-Anti-Spoofing)
and was trained on CelebA-Spoof, which is for non-commercial research use. That repository has no
explicit license, so it is fine for an academic project with citation; clarify licensing before
any commercial use. Bootstrap, Bootstrap Icons and Chart.js are MIT-licensed
(see `app/static/vendor/*/LICENSE*`).
