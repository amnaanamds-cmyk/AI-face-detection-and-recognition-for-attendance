"""Menu of the Android app (native bottom bar + "More" sheet), by role.

The web pages hand it to the app through `FaceAttendApp.setNav(...)` (see base.html), so the app
always shows exactly what the logged-in person may open, in the organization's own words.
"""
from __future__ import annotations

from app.config import settings


def _item(title: str, path: str, icon: str = "dot") -> dict:
    return {"t": title, "p": path, "i": icon}


def app_nav(user, t) -> dict | None:
    if user is None:
        return None
    role = user.role.value
    if role == "student":
        return {"main": [_item("My attendance", "/me", "home")],
                "more": [{"h": "Account", "items": [_item("Change password", "/account/password", "key")]}]}
    school = t.staff == "Teacher"
    if role == "teacher":
        main = [_item("Home", "/", "home"), _item("Attendance", "/sessions", "camera"),
                _item("Timetable", "/timetable", "calendar"), _item(t.people, "/students", "people")]
        more = [{"h": "Daily work", "items": [_item("Start attendance", "/sessions/new", "camera"),
                                              _item("Kiosk (door camera)", "/kiosk", "door"),
                                              _item("Reports & register", "/reports", "chart"),
                                              _item("Analytics", "/analytics", "chart"),
                                              _item("Alerts", "/notifications", "bell"),
                                              _item("Emergency roll call", "/muster", "alert")]},
                {"h": "My " + t.groups.lower(), "items": [_item(t.groups, "/courses", "book"),
                                                          _item("Holidays", "/calendar", "calendar")]},
                {"h": "Account", "items": [_item("Change password", "/account/password", "key")]}]
        return {"main": main, "more": more}
    main = [_item("Home", "/", "home"), _item("Attendance", "/sessions", "camera"),
            _item(t.people, "/students", "people"), _item("Overview", "/overview", "dashboard")]
    more = [{"h": "Daily work", "items": [_item("Start attendance", "/sessions/new", "camera"),
                                          _item("Kiosk (door camera)", "/kiosk", "door"),
                                          _item("Timetable", "/timetable", "calendar"),
                                          _item("Reports & register", "/reports", "chart"),
                                          _item("Analytics", "/analytics", "chart"),
                                          _item("Alerts", "/notifications", "bell"),
                                          _item("Emergency roll call", "/muster", "alert"),
                                          _item("Visitors", "/visitors", "badge")]},
            {"h": "School set-up" if school else "Set-up", "items": [
                _item(t.groups, "/courses", "book"),
                _item(f"{t.staff} attendance", "/staff", "badge"),
                _item(f"Import {t.staffs.lower()} & {t.groups.lower()}", "/setup/staff", "upload"),
                _item(f"Import {t.people.lower()} & photos", "/students/import", "upload"),
                _item("Holidays", "/calendar", "calendar"),
                _item("Users & roles", "/users", "people"),
                _item("Connect phones", "/mobile", "phone"),
                _item("System settings", "/settings", "settings")]},
            {"h": "More tools", "items": [_item("Parent messages", "/messages", "chat"),
                                          _item("Ask FaceAttend", "/ask", "chat"),
                                          _item("Integrity & certificates", "/integrity", "shield"),
                                          _item("Proxy watch", "/proxy", "alert"),
                                          _item("Recognition health", "/recognition-health", "heart")]
             + ([_item("Backup & restore", "/backup", "shield")] if user.is_superadmin and settings.edition != "saas" else [])
             + [_item("Subscription" if settings.edition == "saas" else "License", "/billing", "card")]},
            {"h": "Account", "items": [_item("Change password", "/account/password", "key")]}]
    return {"main": main, "more": more}
