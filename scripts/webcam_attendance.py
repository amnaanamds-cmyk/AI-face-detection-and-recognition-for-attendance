"""Desktop mode: take attendance with a camera attached to the server machine.

    python scripts/webcam_attendance.py --session 3 [--camera 0]

Uses exactly the same pipeline and rules as the web "Live attendance" page, but
reads frames with OpenCV and shows them in a window (needs `opencv-python`, the
non-headless build, for cv2.imshow). Press q to quit, c to close the session.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import database  # noqa: E402
from app.models import ClassSession, SessionState  # noqa: E402
from app.services import attendance as att  # noqa: E402

COLORS = {"marked": (84, 135, 25), "duplicate": (253, 110, 13), "accepted": (84, 135, 25),
          "checking": (7, 193, 255), "unknown": (69, 53, 220), "spoof": (132, 51, 214), "rejected": (125, 117, 108)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", type=int, required=True)
    ap.add_argument("--camera", default="0", help="camera index or video file / RTSP URL")
    ap.add_argument("--interval", type=float, default=0.4, help="seconds between processed frames")
    args = ap.parse_args()

    database.init_db()
    db = database.SessionLocal()
    session = db.get(ClassSession, args.session)
    if session is None:
        print("No such session")
        return 1
    if session.state == SessionState.closed:
        print("Session is closed")
        return 1
    att.start_session(db, session)

    cap = cv2.VideoCapture(int(args.camera) if args.camera.isdigit() else args.camera)
    if not cap.isOpened():
        print("Cannot open camera", args.camera)
        return 1
    last, result = 0.0, {"faces": [], "summary": att.session_summary(db, session)}
    print(f"Taking attendance for {session.course.code} - press q to quit, c to close the session")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if time.monotonic() - last >= args.interval:
            last = time.monotonic()
            result = att.process_frame(db, session, frame)
            for f in result["faces"]:
                if f["state"] in ("marked", "spoof", "unknown") and f["message"]:
                    print(f"[{time.strftime('%H:%M:%S')}] {f['label']}: {f['message']}")
        for f in result["faces"]:
            x, y, w, h = f["bbox"]
            color = COLORS.get(f["state"], (255, 255, 255))
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            cv2.putText(frame, f"{f['label']} [{f['state']}]", (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        s = result["summary"]
        cv2.putText(frame, f"Attended {s['attended']}/{s['expected']} ({s['rate']}%)", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.imshow("Attendance", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("c"):
            n = att.close_session(db, session)
            print(f"Session closed, {n} student(s) marked absent")
            break
    cap.release()
    cv2.destroyAllWindows()
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
