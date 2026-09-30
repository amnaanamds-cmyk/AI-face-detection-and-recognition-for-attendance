# Before you sell it: launch checklist

The software side is ready. These items are not code. Some of them are legal requirements,
and skipping them can cost far more than the product earns. Items marked ⚠️ block a launch.

## 1. Legal and licensing

- ⚠️ **Company and contracts.** Register a business entity, so personal liability is limited.
  Have a lawyer review and adapt `/legal/terms`, `/legal/privacy`, `/legal/dpa` and
  `/legal/consent-form`. These are templates, not legal advice.
- ⚠️ **Biometric laws in each market you sell to.** Face templates are *special category* or
  biometric data almost everywhere:
  - EU/UK **GDPR** Art. 9: explicit consent or another Art. 9 basis; a DPIA (data protection
    impact assessment) per customer; you act as *processor* and sign DPAs. Some EU regulators
    have **fined schools** for face-recognition attendance where a less intrusive method was
    available. Schools are the highest-risk segment in the EU, so let customers decide with
    their DPO, and offer consent plus an alternative (manual marking) for anyone who declines.
  - EU **AI Act**: biometric *identification* systems face strict rules. Attendance with
    consent is typically treated as verification-style use, but get this classified by a lawyer
    before selling in the EU.
  - USA: **BIPA** (Illinois: written consent, public retention policy, private right of
    action with statutory damages per violation), **CUBI** (Texas), Washington, and
    city-level bans (e.g. Portland for private entities in public places). Several states limit
    face recognition in **K-12 schools**. Consider universities and businesses only at first.
  - Also check India DPDP, Brazil LGPD, Pakistan (PECA and pending data-protection law) and
    others, depending on where your customers are.
  - The product already supports compliance: consent recorded per person, automatic retention,
    per-person export, deletion of closed accounts, and an audit log.
- ⚠️ **Model licenses.** YuNet (MIT) and SFace (Apache-2.0) come from the OpenCV Model Zoo and allow commercial use. The research
  anti-spoofing model is **non-commercial**, so never ship it; it is only downloaded with
  `--include-research-antispoof`. The training code in `training/antispoof` is Apache-2.0: keep
  its license file. Ask a lawyer whether the datasets the recognition model was trained on
  create any risk in your market; this area of law is unsettled.
- **Anti-spoofing data**: record your own training data with written consent, or buy a
  commercially licensed dataset (see `training/antispoof/README.md`).
- **Name and trademark**: search trademark registers (USPTO, EUIPO, WIPO) and app stores for
  your `APP_NAME`, and register the domain.
- **Insurance**: professional liability / cyber insurance once you have paying customers.

## 2. Security

- Run the service only behind HTTPS (the SaaS setup does this) with strong `SECRET_KEY`,
  `EMBEDDING_KEY` and admin passwords (the app refuses the demo password in SaaS mode).
- **Keep the camera device under the organization's control** (a school or company laptop, or a
  kiosk tablet in kiosk mode). Liveness checks catch photos and screens held up to the camera,
  and frozen images injected through a virtual camera. Someone with full control of the camera
  device could still inject *video*. That limit applies to all browser-based liveness systems;
  describe it honestly in sales material.
- Nightly off-site database backups, plus a backup of `EMBEDDING_KEY`. Test a restore.
- Security contact address (`/.well-known/security.txt` or in the privacy policy).
- Before larger B2B deals: an independent penetration test. Enterprise buyers will also ask for
  SOC 2 or ISO 27001 later.

## 3. Product quality

- ⚠️ Measure accuracy on real people in a real room: `scripts/evaluate.py` for recognition,
  `training/antispoof/evaluate.py` for liveness. Report honest numbers in marketing.
- Test with a diverse group (skin tones, ages, glasses, headscarves, beards) and fix any group
  that performs clearly worse before launch.
- Test the kiosk on the actual tablets you recommend, in bright and dim light.
- Test the whole Stripe flow in test mode, then once in live mode with a real card (refund
  yourself).

## 4. Business

- Pricing sanity check: a Pro customer at $99/month with about 1,000 people costs a few dollars
  of server time. Keep the margin for support.
- Support: a shared inbox (`SUPPORT_EMAIL`), a short help page or video per use case, and a
  response-time promise you can keep.
- Analytics on the landing page. Use a privacy-friendly tool (Plausible, Umami) if you market
  a privacy-first product.
- A plan for the first 10 customers *before* Product Hunt: local schools, gyms or offices you
  can visit. Product Hunt brings attention, but not reliably customers.

## 5. Launch

- [ ] Landing page on your domain, legal pages filled in (`LEGAL_*`), support e-mail working
- [ ] 30–60 s demo video filmed with people who agreed to appear
- [ ] Gallery images (`docs/launch/`); first comment ready (`docs/PRODUCT_HUNT_LAUNCH.md`)
- [ ] Launch-day discount code created in Stripe
- [ ] You are free all launch day to answer comments
