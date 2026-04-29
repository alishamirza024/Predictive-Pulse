# Deploying Hypertension Detection App to Render

---

## Overview of Files You Need

```
your-project/
├── app.py                  ← Modified (PostgreSQL support added)
├── requirements.txt        ← NEW — Python dependencies
├── Procfile                ← NEW — tells Render how to start the app
├── render.yaml             ← NEW — optional Blueprint config
├── .gitignore              ← NEW — keep secrets out of git
├── .env.example            ← NEW — template for local env vars
├── logreg_model.pkl        ← Your existing ML model (MUST be in repo)
├── templates/              ← Your HTML files go here
│   ├── index.html
│   ├── dashboard.html
│   ├── doctor_dashboard.html
│   ├── login.html
│   └── register.html
└── static/
    └── style.css           ← Your CSS file goes here
```

> **Important:** Flask expects HTML files inside a `templates/` folder and
> CSS/JS inside a `static/` folder. Make sure your project is structured
> this way before pushing to GitHub.

---

## Step 1 — Fix Your Local Project Structure

If your files are currently flat (not in subdirectories), reorganise them:

```
mkdir templates static
mv dashboard.html doctor_dashboard.html index.html login.html register.html templates/
mv style.css static/
```

Your `app.py` already uses `url_for('static', filename='style.css')` so no
HTML changes are needed — Flask will automatically look in `static/`.

---

## Step 2 — Add the New Files to Your Project

Copy the following files into your project root (same folder as `app.py`):

- `app.py`             — replace your existing one (PostgreSQL support added)
- `requirements.txt`   — lists all Python packages
- `Procfile`           — one-line start command for Render
- `render.yaml`        — optional but makes setup faster
- `.gitignore`         — prevents secrets from being committed
- `.env.example`       — reference for local dev environment vars

Your `logreg_model.pkl` **must** be committed to the repo. Render has no
persistent filesystem for uploaded files — the model must ship with the code.

---

## Step 3 — Create a GitHub Repository

1. Go to https://github.com and click **New repository**.
2. Name it (e.g. `hypertension-detection`), set it to **Public** or **Private**.
3. Click **Create repository**.

In your local project folder, run:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
git push -u origin main
```

> **Check:** After pushing, confirm `logreg_model.pkl`, `requirements.txt`,
> and `Procfile` appear on GitHub before continuing.

---

## Step 4 — Create a Render Account

1. Go to https://render.com and click **Get Started for Free**.
2. Sign up with your GitHub account (easiest — it grants repo access automatically).
3. Verify your email if prompted.

---

## Step 5 — Create a Free PostgreSQL Database on Render

Render provides a free PostgreSQL database (no credit card required).

1. In the Render Dashboard, click **New +** → **PostgreSQL**.
2. Fill in:
   - **Name:** `hypertension-db`
   - **Database:** `hypertension_db`
   - **User:** leave as default
   - **Region:** pick the closest to your users
   - **Plan:** Free
3. Click **Create Database**.
4. Wait ~1 minute for it to provision.
5. On the database page, scroll down and **copy the "Internal Database URL"**.
   It looks like: `postgresql://user:pass@dpg-xxxxx-a/hypertension_db`
   Keep this tab open — you'll need it in Step 7.

---

## Step 6 — Create a Web Service on Render

1. In the Render Dashboard, click **New +** → **Web Service**.
2. Click **Connect a repository** — authorise GitHub if prompted.
3. Find and select your repository, then click **Connect**.
4. Fill in the settings:

   | Field            | Value                                              |
   |------------------|----------------------------------------------------|
   | **Name**         | `hypertension-detection` (or anything you like)    |
   | **Region**       | Same region as your database                       |
   | **Branch**       | `main`                                             |
   | **Runtime**      | Python 3                                           |
   | **Build Command**| `pip install -r requirements.txt`                  |
   | **Start Command**| `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120` |
   | **Plan**         | Free                                               |

5. Do **NOT** click Deploy yet — proceed to Step 7 first.

---

## Step 7 — Set Environment Variables

Still on the web service creation page, scroll down to **Environment Variables**
and add the following:

| Key            | Value                                          | Notes                          |
|----------------|------------------------------------------------|--------------------------------|
| `DATABASE_URL` | `postgresql://user:pass@host/dbname`           | Paste the Internal URL from Step 5 |
| `SECRET_KEY`   | Any long random string (e.g. `xK9#mP2@vL7...`) | Use a password manager to generate |

To generate a good SECRET_KEY, run this in your terminal:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> **Never** set `MYSQLUSER`, `MYSQLPASSWORD`, etc. on Render — the modified
> `app.py` will use `DATABASE_URL` automatically when it's present.

---

## Step 8 — Deploy

1. Click **Create Web Service**.
2. Render will clone your repo, install dependencies, and start the server.
3. Watch the **Logs** tab — a successful deploy ends with:
   ```
   [INFO] Booting worker with pid: ...
   [INFO] Worker booting (pid: ...)
   ```
4. Your app URL appears at the top: `https://hypertension-detection.onrender.com`

---

## Step 9 — Verify the Deployment

1. Open your app URL in a browser.
2. Try registering a new account.
3. Run an assessment and check the dashboard.
4. Try downloading a PDF report.

If anything is broken, click the **Logs** tab on Render to see the error.

---

## Troubleshooting Common Issues

### "Application Error" on first load
- Check the **Logs** tab. Most often it's a missing environment variable.
- Make sure `DATABASE_URL` is set correctly (no extra spaces, full URL).

### "ModuleNotFoundError: No module named 'X'"
- Add the missing package to `requirements.txt` and push — Render will rebuild.

### "Model not found / Demo Mode" warning
- Your `logreg_model.pkl` is missing from GitHub.
- Run `git add logreg_model.pkl && git commit -m "add model" && git push`.

### Static files (CSS) not loading
- Confirm `style.css` is inside the `static/` folder.
- Confirm `templates/` folder contains all HTML files.
- Push the corrected structure to GitHub.

### Database tables not created
- The app auto-calls `db.create_all()` on startup via the `with app.app_context()` block.
- If you see table-related errors, check the Render logs for DB connection errors.
- Verify the `DATABASE_URL` Internal URL is from the **same region** as your web service.

### App sleeps after inactivity (Free plan)
- Render's free web services spin down after 15 minutes of inactivity.
- The first request after sleep takes ~30 seconds (cold start). This is normal on the free tier.
- Upgrade to a paid plan or use a free uptime monitor like https://uptimerobot.com to ping the app periodically.

### "postgres://" URL error
- Some Render database URLs start with `postgres://` instead of `postgresql://`.
- The modified `app.py` already handles this fix automatically.

---

## Updating the App After Changes

Render auto-deploys on every push to your `main` branch:

```bash
# Make your changes locally, then:
git add .
git commit -m "describe your change"
git push
```

Render picks this up within seconds and redeploys automatically.

---

## Local Development (MySQL) Still Works

The modified `app.py` only uses `DATABASE_URL` when it's set. For local dev:

1. Copy `.env.example` to `.env` and fill in your MySQL credentials.
2. Install `python-dotenv` (already in `requirements.txt`).
3. Add this to the very top of `app.py` (optional, for auto-loading `.env`):
   ```python
   from dotenv import load_dotenv
   load_dotenv()
   ```
4. Run `python app.py` as usual — it connects to MySQL locally.

---

## Quick Checklist Before Going Live

- [ ] `logreg_model.pkl` is committed to GitHub
- [ ] `templates/` folder exists with all 5 HTML files
- [ ] `static/` folder exists with `style.css`
- [ ] `requirements.txt` is in the project root
- [ ] `Procfile` is in the project root
- [ ] `DATABASE_URL` is set in Render environment variables
- [ ] `SECRET_KEY` is set in Render environment variables
- [ ] Database and web service are in the same Render region
