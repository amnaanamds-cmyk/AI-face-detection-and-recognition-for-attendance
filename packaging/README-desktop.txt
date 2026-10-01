FaceAttend - face recognition attendance
=========================================

Start:   double-click FaceAttend (desktop shortcut or Start menu).
         The app opens in its own window. The attendance server runs in the
         background (icon next to the clock), so closing the window does not
         stop it: phones keep working and the app reopens instantly.
Quit:    right-click the tray icon > Quit FaceAttend.
Start with Windows: tray icon > Start with Windows (installer option).
Login:   admin / admin123  - change the password after the first login.

Camera:  allow camera access the first time (it is remembered).

Phones and tablets (optional, same Wi-Fi):
         in the app open your name (top right) > Mobile app, scan the QR code
         and follow the one-time certificate steps shown there. If Windows
         Firewall asks, allow FaceAttend on PRIVATE networks.

Your data: %LOCALAPPDATA%\FaceAttend  (database, settings, logs).
         Back up this folder. Uninstalling or updating the app keeps it.

Custom anti-spoofing model: copy antispoof.onnx + antispoof.json into
         %LOCALAPPDATA%\FaceAttend\models and restart the app.

Free for up to 25 people without a license key (Admin > License).
