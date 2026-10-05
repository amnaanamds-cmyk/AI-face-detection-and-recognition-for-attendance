# Setting up FaceAttend in a school

Each teacher takes attendance on their own phone. The principal watches the whole school on one page.

```
 Teacher phones (FaceAttend app)          School computer (FaceAttend server)          Principal
 Ms. Ayesha - Mathematics  ──┐                                                    ┌── Overview page
 Mr. Imran  - English      ──┼──>  one database: every class, every day  ─────────┤   (laptop or phone)
 Ms. Nadia  - Physics      ──┘                                                    └── Excel download
```

All attendance goes to one server, the FaceAttend program on the school computer. That
server saves every record by date. Nothing is kept only on a phone.

## 1. The school computer (once)

1. Install `FaceAttend-Setup.exe` from the
   [latest release](https://github.com/amnaanamds-cmyk/AI-face-detection-and-recognition-for-attendance/releases/latest).
   Leave the computer switched on during school hours. FaceAttend starts with Windows.
2. Log in as `admin` / `admin123` and **change the password**. This is the **principal's account**.
3. **Admin → System settings**: set the school name, the required attendance % (default 75)
   and the present/late minutes.
4. Tray icon → **Connect phones / share online** → **Share online**. Teachers' phones can then
   connect from anywhere, on Wi-Fi or mobile data.
   *The address changes whenever sharing is restarted. For an address that never changes, see
   [MOBILE_AND_MESSAGES.md](MOBILE_AND_MESSAGES.md) (a named Cloudflare tunnel or the hosted edition).*

## 2. Teachers, subjects, students

| What | Where | Note |
|---|---|---|
| A login for every teacher | Admin → Users & roles → role **teacher** | e.g. `t.ayesha` with their own password |
| Subjects (courses) | Courses → New | e.g. `MATH-9`, class 9, section A, **teacher = Ms. Ayesha** |
| Students | Students → **Import** (Excel/CSV template) or New | same class/section as the subjects → enrolled automatically |
| Faces | each student's page → Register face | 3-5 photos, or with the phone camera |

A teacher sees and can change **only their own subjects**. The principal (admin) sees everything.

## 3. Teachers' phones

1. Install the app: <https://github.com/amnaanamds-cmyk/AI-face-detection-and-recognition-for-attendance/releases/latest/download/FaceAttend.apk>
2. Open it → **Scan QR code** → scan the QR shown on the school computer.
3. Log in with **their own** teacher account.
4. In class: **Start attendance** → choose the subject → point the camera at the class.
   Students are marked present, late or absent by face. Press **Close** at the end. If the
   teacher forgets, the class closes automatically 2 hours after it ends and absentees are saved.

## 4. The principal's overview

Open **Overview** in the menu (or the button on the dashboard), on the computer or on a phone
logged in as admin.

| Tab | Shows |
|---|---|
| **Today** | every teacher: attendance taken or not yet, each class with present / expected. Refreshes every minute |
| **Teachers** | classes held, their students' attendance %, **% marked by hand** (high = attendance not taken by face), last taken |
| **Subjects** | attendance % per subject, its teacher, and how many students are below the required % |
| **Students** | overall % and **% in every subject** (red = below the required %), with search, class filter and "only below" |

Choose **Today / This week / This month / Whole term** or any dates. **Download Excel** gives
the same information in three sheets (Students, Subjects, Teachers) for the school records.
Day-by-day registers are under **Reports**. Parents of absent students can get an SMS
(Admin → Parent messages).
