# The Snooker Villa — shared snooker/pool booking

Powered by **SHOAM: The Local Marketplace**.

A booking page where every device sees the same tables and the same match-up rail,
backed by Google Sheets.

What it does:
- **Rolling 7-day window** — always shows today + the next 6 days. Each midnight a new
  day appears and the oldest drops off (past bookings are also pruned from the sheet).
- **Booking** requires name + **mobile number** (mandatory).
- **Find a match** — post your name, level (Beginner / Intermediate / Pro) and number
  (email optional). Anyone interested leaves their details and instantly gets
  **WhatsApp / Call / Email** buttons to connect; the poster is also emailed if SMTP is set.
- **Admin** (password-protected) can cancel any booking and remove any match-up.
  Regular visitors can only book and post, not delete.
- **SHOAM** logo + App Store / Google Play download buttons in the header.

```
pool-booking/
├─ app.py              Flask API + serves the page
├─ static/index.html   the booking page (talks to the API)
├─ requirements.txt
├─ Procfile            for Railway / gunicorn
└─ .env.example        the variables you need to set
```

---

## 1. Make a Google Sheet

1. Create a blank Google Sheet. Copy its **Sheet ID** from the URL
   (`docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`).
2. The app creates the `Bookings`, `Players`, and `Interests` tabs automatically
   on first use — you don't need to add anything.

## 2. Make a service account (so the app can write to the sheet)

1. Go to **console.cloud.google.com** → create/select a project.
2. Enable the **Google Sheets API**.
3. **APIs & Services → Credentials → Create credentials → Service account.**
4. Open the service account → **Keys → Add key → JSON.** A `.json` file downloads.
5. Open your Sheet → **Share** → paste the service account's email
   (looks like `name@project.iam.gserviceaccount.com`) → give it **Editor**.

## 3. Email (optional but recommended)

Use a Gmail account for the hall:
- Turn on 2-Step Verification, then create an **App Password**
  (Google account → Security → App passwords).
- That 16-character password is your `SMTP_PASS`.

If you skip this, the app still works — every "I'm interested" is logged to the
`Interests` tab so you never lose a lead. It just won't auto-email.

## 4. Deploy on Railway

1. Push this folder to GitHub (e.g. a new `SHOAM-G/pool-booking` repo).
2. On **railway.app** → New Project → Deploy from GitHub → pick the repo.
3. Add these **Variables**:

| Variable            | Value                                                        |
|---------------------|-------------------------------------------------------------|
| `SHEET_ID`          | the Sheet ID from step 1                                     |
| `GOOGLE_CREDENTIALS`| paste the **entire** contents of the service-account JSON   |
| `ADMIN_PASSWORD`    | password for cancelling bookings / removing match-ups — **change the default `snooker123`** |
| `SMTP_HOST`         | `smtp.gmail.com`            *(optional)*                     |
| `SMTP_PORT`         | `587`                       *(optional)*                     |
| `SMTP_USER`         | `yourhall@gmail.com`        *(optional)*                     |
| `SMTP_PASS`         | the Gmail App Password      *(optional)*                     |
| `FROM_NAME`         | `The Break Room`            *(optional)*                     |

4. Railway gives you a URL like `https://pool-booking-production.up.railway.app`.
   Open it — that's the live page.

## 5. (Optional) Host the page on your DigitalOcean CDN instead

The page can live on your CDN while the API stays on Railway:
- In `static/index.html`, set `API_BASE` (near the top of the script) to your
  Railway URL, e.g. `"https://pool-booking-production.up.railway.app"`.
- Upload `index.html` to your `shoam-tech` bucket. CORS is already enabled.

---

## Run locally

```bash
pip install -r requirements.txt
export SHEET_ID="..."
export GOOGLE_CREDENTIALS="$(cat service-account.json)"
python app.py            # http://localhost:5000
```

## Admin

Tap **🔒 Admin** (top of the booking panel) and enter `ADMIN_PASSWORD`. Admin mode then
shows **Cancel booking** on any reserved slot and **Remove** on any match-up. It stays on
for the browser session; **Log out** clears it. Set a strong `ADMIN_PASSWORD` in Railway.

## Change tables or hours

- **Page:** top of the `<script>` in `index.html` → `TABLES`, `OPEN_HOUR`, `CLOSE_HOUR`.
- **Server:** nothing to change — it accepts whatever the page sends.

## How it stays in sync

Every device polls every 15 seconds and refreshes on focus, so a slot booked at the
counter shows as taken on a customer's phone within seconds. Double-booking is blocked
server-side: if two people grab the same slot at once, the second gets "that slot was
just taken" and the grid refreshes.

## Privacy

A poster's email is stored in the sheet but **never sent to browsers**. The "I'm
interested" button hands the message to the server, which emails the poster — so emails
can't be scraped off the page.
