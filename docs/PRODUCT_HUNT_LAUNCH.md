# Product Hunt launch kit

Everything to paste into Product Hunt, plus a plan for launch day. Replace **FaceAttend** with
your final product name if you change `APP_NAME`.

## Listing

**Name:** FaceAttend

**Tagline** (max 60 characters; pick one):
1. `Attendance by face in seconds, for classes, offices & gyms` (59)
2. `Point a camera at the room. Attendance done.` (44)
3. `Face recognition attendance with photo-spoof protection` (55)

**Topics:** Education, Productivity, Artificial Intelligence, SaaS, Human Resources

**Links:** `https://your-domain` (landing page). Add `?ref=producthunt` so you can count sign-ups in your logs.

**Pricing:** Free trial, then paid (from $29/month; free self-hosted up to 25 people)

**Description** (max 500 characters):

> FaceAttend takes attendance by face. Point any webcam or phone at a classroom, or put a tablet
> by the door, and everyone in view is recognised, checked for liveness (no photos or screens)
> and marked present, late or checked out. Reports go out in Excel, CSV and PDF. It runs in the
> browser, installs as a mobile app and uses the words you use: students, employees or members.
> Privacy-first: no photos stored, consent built in. Hosted or on your own server.

## Maker's first comment

> Hi Product Hunt 👋
>
> FaceAttend started as my final-year project in computer science. I watched lecturers lose
> 10 minutes of every class to roll call, and saw "proxy" attendance (friends signing for each
> other) everywhere, so I built a system that takes attendance for a whole room in one
> glance.
>
> **How it works**
> - A face detector finds every face in the frame, small ones at the back included, and a
>   recognition network turns each face into a 128-number template.
> - A person is only marked after several matching frames, and a liveness check rejects
>   photos and phone screens held up to the camera.
> - Anyone the system isn't sure about shows as *Unknown*. It never guesses, and every decision
>   is in an audit log.
>
> **Who it's for**
> - 🎓 Schools and universities: whole-class attendance, late marking, low-attendance alerts
> - 🏢 Offices: touch-free check-in/out kiosk, shifts, hours on site
> - 🏋️ Gyms, clubs and training centres: members check in by walking in
>
> **Privacy** was a design goal from day one. No photos are stored, only encrypted templates.
> Consent is required before enrolment, templates are deleted automatically, and every person
> can export their data. If you need data to stay in your building, there's a self-hosted
> version (free up to 25 people).
>
> Today's offer: **[e.g. 3 months of Pro at 50% off with code PRODUCTHUNT]**.
>
> I'd love your feedback, especially on the kiosk mode and on what reports you'd want. I'll be
> here all day answering questions!

## Gallery images (1270 × 760)

Ready-made images are in [`docs/launch/`](launch/):

| # | File | Caption |
|---|---|---|
| 1 | `01-landing.png` | Attendance by face, in seconds |
| 2 | `02-dashboard.png` | Today at a glance: present, late, absent and alerts |
| 3 | `03-kiosk.png` | Photos and screens are rejected at the kiosk (face pixelated; replace with your own footage) |
| 4 | `04-report.png` | One-click reports in Excel, CSV and PDF |
| 5 | `05-analytics.png` | Trends and low-attendance alerts |
| 6 | `06-mobile.png` | Installs on Android and iPhone |

Also add a shot of a **successful** kiosk check-in, taken with a colleague who agreed to appear.
Do not use photos of celebrities or strangers in marketing: publicity and privacy rights apply.

Put a **30–60 s video** first in the gallery. It converts much better than screenshots. Film it
yourself, with people who agreed to appear: open a session, walk three people in front of the
laptop, show their names turning green, hold up a printed photo and show it being rejected,
then export the report. Record the screen with OBS and the room with a phone.

Thumbnail: `app/static/icons/icon-512.png` (240 × 240 is enough).

## Launch-day plan

| When | What |
|---|---|
| 2–4 weeks before | Create the "coming soon" page on Product Hunt, collect followers. Find a hunter (or self-hunt). Line up 20+ people who will actually try the product and leave honest comments. **Never ask for upvotes**, which breaks PH rules and gets products demoted. |
| 1 week before | Run the whole sign-up → trial → payment flow in live mode with a real card. Test the kiosk on a real tablet. Prepare replies to likely questions: privacy, accuracy, bias, pricing, offline use. |
| Launch day, 00:01 PT | Launch goes live. Post the first comment immediately. |
| During the day | Reply to every comment within about 15 minutes. Share on LinkedIn, X, relevant subreddits (read each sub's rules), and teacher, HR and gym-owner communities. |
| After | Email everyone who signed up. Turn the feedback into a public roadmap. Write a "what we learned" post. |

## Likely questions, with short answers

* **"Is face recognition for attendance legal?"** Generally yes with informed consent. GDPR
  (EU/UK), BIPA (Illinois), CUBI (Texas) and similar laws set rules. The product enforces
  consent and retention and ships templates, but each customer must comply locally.
* **"What about bias?"** The recognition model was trained on large public face datasets. Its
  accuracy varies with lighting and camera quality. The system never guesses (uncertain means
  Unknown), and staff can correct records. We encourage customers to test on their own
  population first (`scripts/evaluate.py`).
* **"Can I fool it with a photo?"** The liveness check targets printed photos and screens. No
  liveness system is perfect, so the audit log records every decision.
* **"Does it work offline?"** Yes, with the self-hosted edition.
