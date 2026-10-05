# Going live: one website for every school and college

After these steps, any school or college opens your address, clicks **Start free trial** and gets its
own private account. Their teachers, students, faces and records are never visible to other schools.
You manage everything from the **Platform console**.

```
 schools & colleges ──signup──> https://your-address ──> each gets its own private account
                                       │                     (principal, teachers, students)
 teachers' phones ──FaceAttend app─────┘
 you (operator) ──── Platform console: all schools, payments to approve, plans
```

## 1. Put it online (about 15 minutes, no server administration)

1. Create a free account at [render.com](https://render.com) and connect your GitHub account.
2. Click **[Deploy to Render](https://render.com/deploy?repo=https://github.com/amnaanamds-cmyk/AI-face-detection-and-recognition-for-attendance)**.
   Render reads `render.yaml`, which sets up the website and its PostgreSQL database.
3. Render asks for a few values:

| Value | What to enter |
|---|---|
| `ADMIN_PASSWORD` | a strong password for **your** operator login (username `operator`) |
| `LOCAL_PAYMENT_DETAILS` | where schools pay, e.g. `Bank: Meezan Bank \| Account title: Your Name \| IBAN: PK.. \| JazzCash / Easypaisa: 0300-..` |
| `SUPPORT_EMAIL`, `LEGAL_COMPANY_NAME`, `LEGAL_CONTACT_EMAIL` | your contact details, shown on the terms and privacy pages |

4. Click **Apply**. The first build takes about 10 minutes. Your address is then
   `https://faceattend-xxxx.onrender.com`. Open it: the product page with **Start free trial** appears.
5. **Back up `EMBEDDING_KEY`** (Render → faceattend → Environment). Face data is encrypted with it.

Cost: about US$13 a month (web service *starter* + database *basic-256mb*), enough for several
schools. Move the web service to *standard* (2 GB) when you have 20 or more schools.

### Your own address (optional)

Buy a domain (e.g. `faceattend.pk` from PKNIC or any registrar), then Render → faceattend →
**Settings → Custom domains → Add**, and create the DNS record it shows. HTTPS is automatic. Then set
`PUBLIC_BASE_URL` to `https://your-domain` under Environment.

### Other hosts

Any Linux server works with Docker: see [SAAS_DEPLOYMENT.md](SAAS_DEPLOYMENT.md) (app + PostgreSQL +
automatic HTTPS with Caddy, about US$15 a month at Hetzner or DigitalOcean).

## 2. Make the phone app open your service directly

1. GitHub → repository → **Settings → Secrets and variables → Actions → Variables → New variable**:
   name `FACEATTEND_CLOUD_URL`, value your address (e.g. `https://faceattend-xxxx.onrender.com`).
2. Publish a new release (Releases → Draft a new release → tag `v1.2.0`).

The new `FaceAttend.apk` then opens your service on its first start. Teachers just install it and log
in, with no QR code and no address to type. Phones with an older version get the update offered
automatically.

## 3. Getting paid

| How schools pay | What you do |
|---|---|
| **Bank transfer, JazzCash, Easypaisa** (`LOCAL_PAYMENT_DETAILS`) | The school pays and enters the transaction ID on its Billing page. You check your statement and click **Approve** in the Platform console. The plan runs until a date; a year costs 10 months. |
| **Card** (Stripe, outside Pakistan) | Set the `STRIPE_*` variables ([SAAS_DEPLOYMENT.md](SAAS_DEPLOYMENT.md) section 3). Renewal is automatic. |

Prices are in `app/services/billing.py` (`price_month_local`: Starter PKR 3,000 for up to 100 students,
Pro PKR 9,000 up to 1,000, Business PKR 25,000 up to 10,000). Every school starts with a 14-day free trial.
When a paid period ends, recognition pauses, but the school can still see and export its data.

## 4. E-mail (recommended)

For "Forgot password?" links and low-attendance alerts, add `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASSWORD` and `SMTP_SENDER` (e.g. a Gmail app password, Brevo or Mailgun). Without e-mail, a
principal resets teachers' passwords under Admin → Users & roles.

## 5. What a new school does

1. Opens your address → **Start free trial** → chooses *School* or *College or university*.
2. Follows the **Set up** checklist on its dashboard:
   - one Excel sheet creates all teachers (with printable logins) and subjects;
   - class lists from Excel; face photos as a ZIP or with the phone camera;
   - teachers install the app and log in;
   - first attendance.
3. The principal watches **Overview**; parents get absence SMS from the school's own phone
   ([MOBILE_AND_MESSAGES.md](MOBILE_AND_MESSAGES.md)).

## 6. Before you sell

- **Business registration:** register a sole proprietorship or company, and open a business bank account for payments.
- **Privacy:** face data is personal data. Have the terms, privacy policy and data processing
  agreement (`/legal/...`) reviewed by a lawyer, and keep schools collecting parents' consent (the
  sign-up form requires it).
- **Signing key:** create your own Android signing key (README → Download) before the first school installs the app.
- **Backups:** Render keeps daily database backups on paid plans; also download one regularly.
