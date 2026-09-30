# Running the hosted (SaaS) edition

This guide puts the product on the internet at `https://your-domain`. Customers sign up there,
get a 14-day trial and pay by card through Stripe. Each customer is an isolated organization,
with its own people, face gallery, settings and reports.

> The self-hosted edition (customers run it on their own PC or server) is covered in
> [DEPLOYMENT.md](DEPLOYMENT.md). Licensing it is covered in section 6 below.

## 1. What you need

| | |
|---|---|
| Server | Linux VPS with 4 vCPU and 8 GB RAM (e.g. Hetzner CPX31, DigitalOcean 8 GB). This handles roughly 30–50 cameras sending frames at the same time. No GPU needed. |
| Domain | e.g. `attend.yourbrand.com`, with an **A record** pointing to the server's IP |
| Stripe account | activated for live payments in your country |
| E-mail sender (optional) | any SMTP service (Postmark, SES, Mailgun) for low-attendance alerts |
| Backups | object storage or a second server for nightly database dumps |

## 2. Install

```bash
# on the server (Ubuntu 22.04/24.04)
curl -fsSL https://get.docker.com | sh
git clone <your repository> attendance && cd attendance/deploy
cp saas.env.example saas.env
nano saas.env          # fill in every value (see below)
docker compose -f docker-compose.saas.yml --env-file saas.env up -d --build
```

Caddy gets a Let's Encrypt certificate for `DOMAIN` automatically. HTTPS is required, because
browsers only allow camera access on secure pages. Open `https://DOMAIN`: you should see the
product page.

Important values in `saas.env`:

* `SECRET_KEY`, `EMBEDDING_KEY`, `DB_PASSWORD`: long random strings
  (`python -c "import secrets;print(secrets.token_urlsafe(48))"`). **Back up `EMBEDDING_KEY`
  separately.** Face templates are encrypted with it, and if it is lost, every customer has to
  re-enroll every face.
* `ADMIN_USERNAME` / `ADMIN_PASSWORD`: *your* operator account. It opens the **Platform
  console** (`/platform`), where you see all customer organizations, change plans, suspend
  accounts and delete face data of closed accounts. The app refuses to start with the demo
  password.
* `APP_NAME`: your product name. Check that the name is free as a trademark and a domain in
  your markets before you launch.
* `LEGAL_*`: shown in the Terms, Privacy Policy and DPA at `/legal/...`.

## 3. Stripe

1. Stripe Dashboard → **Product catalog** → create one product with three **recurring monthly
   prices**: Starter $29, Pro $99, Business $299. You can change the amounts, but also change
   `price_month_usd` in `app/services/billing.py` so the pricing page matches. Put the three
   `price_...` ids into `STRIPE_PRICE_STARTER/PRO/BUSINESS`.
2. **Developers → API keys** → secret key → `STRIPE_SECRET_KEY`.
3. **Developers → Webhooks → Add endpoint** → `https://DOMAIN/billing/webhook`, with events
   `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`. Copy the signing
   secret to `STRIPE_WEBHOOK_SECRET`.
4. **Settings → Billing → Customer portal**: enable it, so customers can change their plan,
   update their card and download invoices.
5. Restart: `docker compose -f docker-compose.saas.yml --env-file saas.env up -d`.

Test the whole flow in Stripe **test mode** first: sign up → Billing → choose a plan →
pay with card `4242 4242 4242 4242`. The plan must change to *active*. If a payment fails, the
account turns *past due*. Recognition then pauses, but customers can still see and export their
data.

## 4. Anti-spoofing model

The image ships with the face detector and recognizer. It does **not** include an
anti-spoofing CNN, because the free research model is non-commercial. Without one, liveness uses
the head-movement test. When you have trained your own model
([training/antispoof/README.md](../training/antispoof/README.md)), put `antispoof.onnx` and
`antispoof.json` into `models/` before `docker compose ... up -d --build`.

## 5. Operating it

| Task | How |
|---|---|
| Update | `git pull && docker compose -f docker-compose.saas.yml --env-file saas.env up -d --build`. Database changes are applied automatically at start-up. |
| Backups | nightly: `docker compose -f docker-compose.saas.yml exec -T db pg_dump -U attendance attendance \| gzip > backup-$(date +%F).sql.gz`, copied off the server. Test a restore once a quarter. |
| Logs | `docker compose -f docker-compose.saas.yml logs -f app` |
| Uptime | an external monitor (UptimeRobot, Better Stack) on `https://DOMAIN/login` |
| Closed accounts | Platform console → **Suspend**, wait for the notice period in your Terms, then **Delete face data** |
| Scaling | Run **one** app process per server, because live face tracks are held in memory. Scale up with more CPU first. Beyond about 100 cameras at once, run several servers and pin each customer to one. |

## 6. Selling self-hosted licenses

Self-hosted installs are free for up to 25 people and need a signed license key above that.
Only you can issue keys:

```bash
# once: create your signing key pair (keep the private key OFF the server, backed up offline)
python scripts/license_tool.py keygen --private ~/attendance-license-private.pem
git add app/license_public_key.pem && git commit -m "License public key"   # ship the public key

# for each customer
python scripts/license_tool.py issue --private ~/attendance-license-private.pem \
    --licensee "City College" --max-people 2000 --expires 2027-09-30
```

The customer pastes the key into **Admin → License**.
