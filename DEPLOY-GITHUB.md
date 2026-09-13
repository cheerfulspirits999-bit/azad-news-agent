# 🚀 Azad Daily News Agent — GitHub Setup (click-by-click)

This makes the agent run **every hour, 24/7, on GitHub's free servers**.
After this, it posts even when your tab is closed, your computer is off,
or you are asleep. Takes about 20–30 minutes. No coding needed.

---

## Part A — Create your free GitHub account

1. Open **https://github.com/signup**
2. Enter your **email**, choose a **password**, choose a **username** (anything, e.g. `azaddaily-news`).
3. GitHub emails you a code — enter it to verify.
4. Skip any "personalize your experience" questions.

## Part B — Create the repository (your agent's new home)

1. Once signed in, click the **+** at the top-right → **New repository**.
2. Repository name: type `azad-news-agent`
3. ⚠️ Select **Private** (very important — the package contains your Zapier link).
4. Do NOT tick any boxes (no README, no .gitignore, no license).
5. Click the green **Create repository** button.

## Part C — Upload the agent

1. In this chat, download **azad-daily-news-agent.zip**, then unzip it:
   - Windows: right-click the zip → **Extract All…**
   - Mac: double-click the zip.
   - You should see these items inside: `.github` folder, `news_agent` folder,
     `DEPLOY-GITHUB.md`, `START-HERE.md`, `requirements.txt`.
2. On your new empty repository page, click the link that says
   **"uploading an existing file"** (inside the "import code" sentence).
3. Open your unzipped folder and **drag ALL 5 items** into the browser box.
   - ⚠️ The `.github` folder MUST be included — it holds the automatic hourly schedule.
   - Can't see `.github`? Mac: press **Cmd + Shift + .** (dot) in Finder to reveal it.
     Windows: it is normally visible; if not, use Plan B at the bottom of this page.
4. Wait for uploads to finish, scroll down, click green **Commit changes**.

## Part D — Test it right now

1. At the top of your repository page, click the **Actions** tab.
   - If it asks to enable Actions, click **"I understand my workflows, go ahead and enable Actions"**.
2. On the left, click **news-cycle**.
3. On the right, click **Run workflow** → then the green **Run workflow** button.
4. Refresh after ~2 minutes. Yellow circle ⏳ → green tick ✅ = SUCCESS.
   (A red ❌ means something failed — GitHub will also email you. Screenshot it and message me.)
5. Check your Facebook Page — if there was a qualifying fresh story, a new post appears.

## Part E — Final step (important!)

Go back to the Arena chat and type **"GitHub is running"**.
I will then permanently switch off my sandbox copy so you never get
double posts. Until you tell me, my copy is switched OFF anyway to be safe.

---

## After this

- It runs **every hour automatically, forever** — tab closed or not.
- Every run saves the agent's memory (what was already posted) back to the
  repo, so it never posts duplicates.
- If a run fails, GitHub **sends you an email** — forward/screenshot it to me.
- ⚠️ Never upload this package to a second repository — only ONE copy may run.

## Plan B — if the `.github` folder refuses to upload

1. In the repository, click **Add file** → **Create new file**.
2. In the filename box, type exactly:
   `.github/workflows/news-cycle.yml`
   (GitHub creates the folders automatically when you type the slashes.)
3. Copy the entire APPENDIX below into the big text box.
4. Click **Commit changes**, then continue with Part D.

---

## APPENDIX — contents of news-cycle.yml (for Plan B copy-paste)

```yaml
name: news-cycle
on:
  schedule:
    - cron: "10 * * * *"
  workflow_dispatch: {}
permissions:
  contents: write
concurrency:
  group: news-cycle
  cancel-in-progress: false
jobs:
  cycle:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - name: run one monitor + publish cycle
        run: python3 news_agent/autod.py --once
      - name: commit agent memory
        if: always()
        run: |
          git config user.name "azad-news-agent"
          git config user.email "agent@localhost"
          git pull --rebase origin "$GITHUB_REF_NAME" || true
          git add news_agent/state news_agent/stories
          if ! git diff --cached --quiet; then
            git commit -m "cycle $(date -u +%FT%TZ)"
            git push
          fi
```
