# Deploying in a real department

This guide takes the system from a laptop to daily use in classrooms.

## 1. Hardware

| Role | Minimum | Recommended |
|---|---|---|
| Server (runs AI + database) | any 4-core PC/laptop, 8 GB RAM, Windows 10+/Ubuntu 22.04+/macOS | 8-core desktop or small server; a GPU is not required |
| Classroom camera device | laptop with webcam, or a phone/tablet with a browser | USB 1080p webcam on a tripod at the front, 1.5–2 m high, facing the students |
| Network | classroom devices on the same LAN / Wi-Fi as the server | wired server, 5 GHz Wi-Fi |

Camera placement matters more than anything else:
- Faces should be at least about 40 px wide in the image. With 1080p this reaches roughly 8–10 m.
- Mount the camera at face height or slightly above, not far above the students.
- Avoid a bright window behind the students.
- For attendance at the door, place the camera at the entrance so students pass it one by one. This gives the highest accuracy.

## 2. Install the server

**Option A: single PC (SQLite, simplest)**

```bash
./start.sh --lan          # Windows: start.bat --lan
```

Open the printed `https://<server-ip>:8443` address on the classroom device. Accept the certificate
warning once, or install `data/tls/cert.pem` as a trusted certificate on the classroom devices. For a
real domain certificate, use `python run.py --cert fullchain.pem --key privkey.pem`.

**Option B: server with PostgreSQL (Docker)**

```bash
export SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
export DB_PASSWORD=<strong password>
docker compose up -d
```

> **Keep `SECRET_KEY` safe and never change it.** It encrypts the face templates and signs logins.
> If it is lost, every student has to re-enrol.

## 3. First-day setup (admin)

1. Log in as `admin / admin123` and **change the password**.
2. **Admin → Users**: create one account per teacher.
3. **Courses**: create the courses (code, name, semester, section, teacher).
4. **Students → Bulk import → Class list**: upload the Excel/CSV list from the registrar. Download
   the template from that page. Students are enrolled automatically in courses with the same
   department, semester and section.
5. Collect **written biometric consent**. A template form is at the end of this document. Tick
   consent per student, or import a *Consent* column.

## 4. Enrolment day (register faces)

Do this in good light, not in the classroom.

- **Webcam (recommended):** Students → *student* → Register face → *Auto-capture 5*. Ask the student
  to look straight, slightly left, slightly right, and slightly up or down. It takes about 20 seconds
  per student.
- **Photos:** collect 3–5 photos per student with a phone. Put them in a folder per Student ID
  (`BSCS-2023-001/1.jpg …`), zip it, and upload it under **Bulk import → Face photos**. Or run
  `python scripts/import_data.py --faces photos/`.

The system refuses unusable enrolments with a reason:
- no face found
- two people of similar size in the photo
- photos that show different people
- a face that is already registered to another student

Check the report and redo those students.

## 5. Calibrate on your own students (strongly recommended)

Before using the system for real grading, measure it on your own class, then pick the threshold
with the best trade-off:

```bash
# use the enrolment photos: dataset/<student_id>/*.jpg
python scripts/evaluate.py --data dataset --out results/class7A
```

Open `results/class7A/threshold_sweep.csv` and choose the `MATCH_THRESHOLD` where **FAR is 0**
(nobody is marked as someone else) and FRR is lowest. Set it in **Admin → System settings**. The
default of 0.45 came from a public benchmark (README §5).

Also run the full system replay on these photos plus a few printed photos or phone screens of
students:

```bash
REAL_FACES_DIR=dataset SPOOF_DIR=spoof_photos pytest tests/test_real_data.py -s
```

## 6. Taking attendance (teacher)

1. **Attendance → Create session**: choose the course and start time, and keep liveness on. Click
   **Create & start**.
2. On the live page, choose **1080p** for a large room, then click **Start camera**.
3. Students look at the camera for 2–3 seconds. Box colours mean:
   - green: marked
   - blue: already marked
   - yellow: identifying or checking liveness
   - red: unknown
   - pink: spoof suspected
4. At the end, click **Close session**. Everyone not seen is marked *Absent*, and low-attendance
   alerts are created.
5. Correct anything on the session page (Excused, Leave, missed students). Every automatic decision
   is in **Admin → AI audit log**.

**Liveness modes** (Admin → System settings):
- `auto`/`cnn`: passive anti-spoofing CNN. Students only look at the camera.
- `cnn+motion`: the CNN **and** a head turn. Use this when proxy attendance is a real problem.
- `motion`: head turn only. Used if the CNN model is not installed.

## 7. Operations

- **Backups:** `python scripts/backup.py` (SQLite online copy or `pg_dump`). Schedule it daily (Task
  Scheduler / cron) and keep copies encrypted and off the server.
- **Updates:** stop the server, `git pull`, `pip install -r requirements.txt`, then start it again.
  New tables are created automatically.
- **Performance:** on a CPU, 10–15 faces per frame at 2 frames/s is comfortable. For bigger rooms,
  use two cameras, each with its own browser tab on the same session.
- **Model check:** Admin → System settings shows whether the face models and the anti-spoofing model
  are installed and which liveness method is active.

## 8. Consent form template

> I, ______________ (Student ID ________), agree that my facial image is processed by the
> department's attendance system. A mathematical template of my face (not the photo) is stored in
> encrypted form and used **only** to record my class attendance. I can withdraw my consent at any
> time and ask for my face data to be deleted; attendance will then be recorded manually.
> The data is deleted when I graduate or leave the programme.
>
> Signature: ______________ Date: __________
