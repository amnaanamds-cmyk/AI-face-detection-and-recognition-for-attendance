# System architecture

## 1. Components

```
┌──────────────────────────── Browser (admin / teacher / student) ─────────────────────────────┐
│  Dashboard · Students · Face registration · Courses · Sessions · Live camera · Analytics ·   │
│  Reports · Settings                (Jinja2 + Bootstrap; camera via getUserMedia, ~2 fps)      │
└───────────────┬──────────────────────────────────────────────────────────────┬───────────────┘
                │ HTML forms / pages                              JSON: POST /api/sessions/{id}/frame
┌───────────────▼──────────────────────────────────────────────────────────────▼───────────────┐
│ FastAPI  (app/routers)  – session-cookie auth, role checks (admin / teacher / student)        │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Services (app/services)                                                                       │
│   faces.py        enrollment, encrypted templates, gallery cache                              │
│   attendance.py   session lifecycle, status rules, duplicate prevention, live frame handling  │
│   analytics.py    KPIs, trends, rankings          reports.py   daily/monthly/session exports  │
│   notifications.py low-attendance alerts (+SMTP)  app_settings.py runtime-configurable rules  │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ AI / computer vision (app/vision)                                                             │
│   backends.py  YuNet (multi-scale) + SFace + MiniFASNet (OpenCV DNN) | FakeBackend for tests  │
│   pipeline.py  FaceTracker (IoU) → per-face embed → match → vote → liveness                   │
│   matcher.py   Gallery: cosine similarity, threshold, margin → student / unknown             │
│   liveness.py  anti-spoofing CNN + 3-D structure-from-motion test + sharpness                 │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ SQLAlchemy 2  →  SQLite (default) / PostgreSQL                                                │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

## 2. Live attendance sequence

```
Browser                    /api/sessions/{id}/frame          Pipeline                        DB
  │ capture JPEG (every 500 ms)      │                           │                            │
  │─────────────────────────────────►│ decode                    │                            │
  │                                  │──────────────────────────►│ YuNet: all faces + 5 pts   │
  │                                  │                           │ tracker: IoU → track id    │
  │                                  │                           │ SFace embed (aligned face) │
  │                                  │                           │ gallery.match → id / None  │
  │                                  │                           │ votes (3 of last 5)        │
  │                                  │                           │ liveness update            │
  │                                  │◄──────────────────────────│ FaceResult per face        │
  │                                  │ confirmed & live & not yet marked?                     │
  │                                  │   mark_attendance: enrolled? session open? existing?   │
  │                                  │──────────────────────────────────────────────────────►│ INSERT attendance
  │                                  │   (UNIQUE(student, session) guards races)              │ INSERT recognition_event
  │◄─────────────────────────────────│ faces[bbox,label,state,message] + session summary      │
  │ draw boxes / labels / counters   │                           │                            │
```

Face-state machine per track (what the UI shows):

```
            ┌──────── votes disagree / below threshold ────────┐
            ▼                                                   │
new face → Identifying… ──3 agreeing votes──► <Name> (checking liveness: "move head")
            │                                        │ live            │ flat / blurry
            │ 5 frames, never matched                ▼                 ▼
            ▼                                   marked / already   Spoof suspected (logged)
         Unknown (logged)                       marked / not enrolled
```

## 3. Database schema

```
users (id, username UQ, password_hash, full_name, email, role[admin|teacher|student],
       student_id → students, is_active, created_at)
students (id, student_code UQ, roll_number, name, department, semester, section, email, phone,
          consent_given, is_active, registration_date)
face_embeddings (id, student_id → students ON DELETE CASCADE, embedding [encrypted BLOB],
                 model_name, quality, created_at)
courses (id, code UQ, name, department, semester, section, teacher_id → users)
enrollments (id, student_id → students, course_id → courses, UQ(student_id, course_id))
sessions (id, course_id → courses, date, start_time, duration_minutes, present_window_minutes,
          late_window_minutes, liveness_required, state[scheduled|active|closed], created_by, created_at)
attendance (id, student_id, session_id, course_id, date, marked_at,
            status[present|late|absent|excused|leave], confidence, liveness_score,
            method[face|manual|auto-absent], note,  UQ(student_id, session_id))
recognition_events (id, session_id, student_id, event[marked|duplicate|unknown|spoof],
                    similarity, liveness_score, created_at)
notifications (id, student_id, course_id, level, message, is_read, created_at)
app_settings (key PK, value)
```

```
users 1─* courses 1─* sessions 1─* attendance *─1 students 1─* face_embeddings
                  1─* enrollments *─1 students
```

Design decisions

* **Duplicate prevention is enforced twice**: the service checks for an existing record, and the
  `UNIQUE(student_id, session_id)` constraint guarantees it even if two frames race.
* **Only embeddings are stored, encrypted.** Raw enrollment photos are never written to disk or DB.
* **Model name is stored per template.** If the recognition model is changed, old templates are
  ignored automatically (they are not comparable) and students must be re-enrolled.
* **Attendance % excludes Excused/Leave** from the denominator.

## 4. Configuration of the AI thresholds

| Setting | Default | Effect |
|---|---|---|
| `DETECTION_SCORE` | 0.85 | YuNet confidence; lower finds more (smaller/blurred) faces but more false detections |
| `MIN_FACE_SIZE` | 40 px | ignore faces too small to recognise reliably |
| `MATCH_THRESHOLD` | 0.45 | SFace cosine threshold; higher → lower FAR, higher FRR (chosen from real-data measurements) |
| `LIVENESS_MODE` | auto | `cnn` (anti-spoofing CNN, passive), `motion` (head turn), `cnn+motion`; auto = cnn if the model is installed |
| `ANTISPOOF_THRESHOLD` | 0.7 | median CNN P(live) needed to accept; below 0.3 = spoof |
| `MATCH_MARGIN` | 0.05 | best student must beat the runner-up by this much |
| `VOTES_REQUIRED` | 3 | agreeing frames (of the last 5) before marking |
| `LIVENESS_MOTION_THRESHOLD` | 0.12 | 3-D motion needed (≈ 20° head turn) |
| `LIVENESS_MIN_FRAMES` / `LIVENESS_TIMEOUT` | 6 / 12 s | observation window before a decision |

## 5. Extending

* **Another detector / recogniser** (RetinaFace, ArcFace/InsightFace via onnxruntime): implement
  `detect()` and `embed()` (see `VisionBackend` in `app/vision/base.py`) and select it in
  `get_backend()`.
* **Deep-learning anti-spoofing**: add a model call inside `LivenessChecker.update` (or a second checker)
  and combine its score with the geometric cue.
* **Multi-classroom**: each camera/browser simply posts frames for its own session id; the central
  database already aggregates all sessions. For several server processes, move `live_trackers` to Redis.
