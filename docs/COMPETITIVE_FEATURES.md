# What sets FaceAttend apart

Five features that typical attendance products (US school and workforce systems) do not
offer. At most, a product has one of them on its own:

| # | Feature | Where | The problem it solves |
|---|---|---|---|
| 1 | **Attendance forecast & recovery planner** | student page, student portal, dashboard "At risk this term" | Others report the past. FaceAttend tells a student *"you must attend 16 of the remaining 20 classes"* while there is still time, and explains why ("often absent on Mondays", "attendance dropping") |
| 2 | **Tamper-evident ledger + verifiable certificates** | Admin → Integrity & certificates, `/verify` | Attendance records can normally be edited silently. Here every change is hash-chained, so direct database edits are detected. Students get signed certificates that a scholarship board or employer can verify by scanning a QR code |
| 3 | **Proxy watch** | Admin → Proxy watch | Catches what face recognition alone misses: the same face twice in one frame (photo or look-alike), one person recognised in two rooms at the same time, staff who mark many people present by hand |
| 4 | **Two-way parent SMS** | Admin → Parent messages | Parents text **STATUS**, **REPORT** or **LEAVE** (or HAAZRI / CHUTTI) to the school phone and get an instant answer from any basic phone, without internet or an app. The SMS go out free from the school's own SIM |
| 5 | **Ask FaceAttend** (optional, Claude) | Ask | *"Who will fall below 75% this term and what must they do?"*, asked in English or Urdu, is answered from the real records through read-only tools |

These sit on top of the core: multi-face recognition with liveness checks (including
frozen-frame injection), kiosk check-in/out, Excel/PDF reports, a desktop app, an Android
app, and online sharing.

## Five-minute demo script

1. **Recognition (1 min).** Open a session. Two people walk in and are marked within about 2 s.
   Hold up a phone photo of a registered student: it shows "Spoof suspected". Hold a photo next to that
   student: it shows "Same person twice?", nobody is accepted, and an alert appears in Proxy watch.
2. **Forecast (1 min).** Open an at-risk student (the dashboard lists them): "Must attend 43 of
   the remaining 48 classes", plus the patterns. Log in as the student to show the same plan in their portal.
3. **Integrity (1 min).** Admin → Integrity shows everything verified. In a database tool, change one
   "absent" to "present" and reload: FaceAttend names the record and the change. Then download a
   certificate, scan its QR code with a phone and show "Genuine". Edit one number in the code and show "Not valid".
4. **Parents (1 min).** Close the session. The absent student's parent gets an SMS. The parent replies
   **STATUS** from a basic phone and gets the answer back. Then the parent sends **CHUTTI bukhar**,
   and you click *Approve leave*.
5. **Ask (1 min).** Ask *"اس ہفتے سب سے زیادہ غیر حاضر کون رہا؟"* (Who was absent most this week?) and
   *"Which weekday has the most absences?"*.

## Honest limits (say them before the judges ask)

- Forecasts assume the recent pattern continues and the term length you set. They are a planning aid, not a verdict.
- The ledger detects tampering. It cannot prevent someone with full server access from deleting everything,
  which is why the fingerprint is printed on reports and certificates.
- Proxy alerts are signals for a person to review, never automatic punishments.
- Ask FaceAttend sends the question and the needed figures to Anthropic's API. It is off by default
  and needs an API key.
- Two-way SMS needs an Android phone with a SIM running the FaceAttend app. Google Play limits SMS
  permissions, so distribute that build directly.
