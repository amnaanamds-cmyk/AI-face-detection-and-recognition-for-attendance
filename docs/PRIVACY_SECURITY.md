# Privacy, security and ethics

Facial templates are **biometric data**. The system is designed so that a university could defend it
to students, a data-protection officer or an ethics committee.

## What is stored

| Data | Stored? | How |
|---|---|---|
| Enrollment photos | **No** | decoded in memory, turned into an embedding, discarded |
| Live camera frames | **No** | processed in memory per request, never written |
| Face embeddings (128 floats) | Yes | encrypted with Fernet (AES-128-CBC + HMAC-SHA256); key derived from `EMBEDDING_KEY` or `SECRET_KEY` |
| Attendance records | Yes | status, time, similarity score, liveness score, method |
| Recognition events | Yes | audit trail (marked / duplicate / unknown / spoof) without images |
| Passwords | Hash only | PBKDF2-HMAC-SHA256, 240 000 iterations, random salt |

## Controls implemented

* **Consent gate** – faces cannot be enrolled unless *consent given* is recorded for the student.
* **Right to erasure** – *Delete face data* removes all templates; deleting a student removes all of
  their data (cascade).
* **Role-based access** – admins manage everything; teachers only see their own courses; students only
  their own records. APIs return 401/403 accordingly.
* **Session security** – signed, `SameSite=Lax` session cookie with 8 h lifetime; login `next` parameter is
  restricted to local paths (no open redirect).
* **No template export** – there is no UI or API that returns embeddings.
* **Audit log** – every automatic decision is logged for review of disputes and spoof attempts.
* **Human in the loop** – teachers can override any status; the camera never makes the final
  disciplinary decision on its own.

## Deployment checklist

1. Set a long random `SECRET_KEY` (and optionally a separate `EMBEDDING_KEY`) – keep them out of git.
   If the key is lost, templates cannot be decrypted and students must re-enrol.
2. Change the default admin password on first login.
3. Serve over **HTTPS** (required by browsers for camera access on non-localhost, and protects cookies).
4. Restrict the database file / server to authorised staff; back it up encrypted.
5. Publish a privacy notice: purpose (attendance only), retention (e.g. delete templates at graduation),
   contact person, how to opt out (manual attendance alternative).
6. For testing with real people obtain written consent and, where required, ethics approval.

## Known risks and mitigations

| Risk | Mitigation in this project | Remaining risk |
|---|---|---|
| Presentation attack (photo / screen) | liveness test, audit log, teacher present | video replay / aggressive photo motion (see EVALUATION.md) |
| Misidentification (wrong student marked) | threshold + margin + 3-frame voting, manual override | depends on threshold; measure FAR |
| Demographic bias of the recognition model | evaluate FRR per group on your own data | pre-trained model bias cannot be fully removed |
| Function creep (surveillance) | system only processes during an active session started by a teacher | organisational policy needed |
| Key compromise | keys only in environment/`data/.secret_key` (0600) | protect the server |
