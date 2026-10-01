# Phones, the Android app and parent messages

## 1. Connect phones

FaceAttend runs on one computer (the desktop app, with its tray icon next to the clock). Phones
connect to it in one of two ways. Both are on the **Connect phones / share online** page (tray
icon menu, or your name → *Mobile app*).

| | Share online | Same Wi-Fi |
|---|---|---|
| Works from | anywhere (mobile data, other Wi-Fi) | the school Wi-Fi only |
| Address | `https://something.trycloudflare.com` | `https://192.168.x.x:8443` |
| Setup on each phone | none | install the server certificate once |
| Needs internet | yes | no |
| Note | the address changes when sharing is restarted | the address changes if the PC's IP changes |

**Share online** gives a public link, so change the `admin` password first (the app insists) and
use strong passwords for all accounts. For a **permanent address** (e.g. `attendance.myschool.pk`),
either set up a named Cloudflare Tunnel with your own domain (free Cloudflare account,
`cloudflared tunnel create`), or run the hosted edition ([SAAS_DEPLOYMENT.md](SAAS_DEPLOYMENT.md)).

## 2. The Android app

*Download Android app* on the Connect phones page, or `FaceAttend.apk` from the GitHub release or
Actions artifacts.

1. Open the APK on the phone. Android may ask to allow installs from this source.
2. Open FaceAttend → **Scan QR code** → scan the QR code shown on the computer.
3. Log in as usual. Taking attendance with the phone camera, reports and everything else work
   like on the computer.

The app is a native shell around the web app. It adds:
- camera permission for live attendance and face registration;
- file upload (spreadsheet / ZIP import) and report downloads (Downloads folder);
- WhatsApp, SMS and phone links open the right app;
- pull down to refresh, plus a clear screen when the server cannot be reached (*Try again* /
  *Change server*);
- the **SMS sender** for parent messages (below).

### Opening and changing it in Android Studio

`File → Open… → choose the android folder`. Android Studio downloads Gradle and the Android SDK
parts it needs. Press ▶ to run it on a phone (USB debugging) or an emulator.

| What | Where |
|---|---|
| App name, texts | `app/src/main/res/values/strings.xml` |
| Colours | `res/values/colors.xml` (`brand` = toolbar) |
| Icon | `res/mipmap-*/ic_launcher.png` (or *New → Image Asset*) |
| Package name / version | `app/build.gradle.kts` (`applicationId`, `versionCode`, `versionName`) |
| Web view behaviour | `MainActivity.kt` |
| SMS sender | `SmsGatewayService.kt`, `GatewayActivity.kt` |

To publish on **Google Play**: change `applicationId` to your own (e.g. `pk.yourschool.faceattend`),
build a signed bundle (*Build → Generate Signed Bundle / APK*) with your own key, and fill in the
Play Console forms (privacy policy URL: `/legal/privacy`, data safety: camera and face data are
sent to your server). Google Play allows the `SEND_SMS` permission only for default SMS apps.
For the Play version, remove the SMS sender (delete the permission and the two classes) and use
Twilio or the manual WhatsApp buttons instead. The APK you share directly can keep it.

## 3. Messages to parents

### Set up
1. Add each student's **parent / guardian mobile** (and e-mail if you like) on the student's
   Edit page, or import a spreadsheet with a column such as `Parent phone`, `Father mobile` or
   `Guardian phone`. Local numbers like `0300 1234567` are fine; set the country code once.
2. **Admin → Parent messages**: choose *Send a message when a student is: Absent* (or *Absent or late*),
   write the text in any language, and choose how to send it:

| Channel | Cost | How |
|---|---|---|
| **School phone SIM** (Android app) | your normal SMS rate / bundle | pair a phone: app ⋮ → *SMS sender* → *Scan pairing code* → *Start sending messages* |
| **WhatsApp / SMS via Twilio** | paid per message | set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_SMS_FROM` / `TWILIO_WHATSAPP_FROM` in `.env` |
| **Manual** | free | the outbox shows a WhatsApp button and an SMS button per message |
| **E-mail** (extra) | free | `SMTP_*` settings in `.env` |

3. Use *Send a test message* to check it works.

### What happens
When a class session closes (by hand, or automatically at the end of a scheduled class), every
absent student whose guardian has a mobile number gets one message. The **Outbox** shows each
message: pending → sent, or failed with the reason (no credit, no signal, wrong number …) and a
retry button. A message is never sent twice for the same class. Teachers can also press
**Message parents** on a closed session.

The SMS phone checks for new messages every 20 seconds. Keep it charged and online, and exclude
FaceAttend from battery saving if the phone stops it (*Settings → Apps → FaceAttend → Battery →
Unrestricted*).

Default message (placeholders are filled in):
`Dear {parent}, {student} was {status} in {group} on {date}. - {org}`
