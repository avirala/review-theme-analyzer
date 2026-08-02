# App Review Theme Analyzer — Web

A password-gated Streamlit web app version of the review-analysis tool:
paste an App Store ID, get themes/sub-themes back in the browser with CSV/
Excel downloads. Deployed for free on Streamlit Community Cloud.

Also usable as a CLI (`python analyze_reviews.py --help`) — see
`review_analyzer/` for the underlying package.

## Deploy it yourself (free, ~10 minutes)

### 1. Push this folder to your own GitHub repo

```bash
cd review-theme-analyzer
git init
git add .
git commit -m "Initial commit"
```

Create a new **empty** repo on [github.com/new](https://github.com/new) (public or
private — Streamlit Community Cloud can deploy from either), then:

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

### 2. Deploy on Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. Click **New app**, pick your repo, branch `main`, main file path `streamlit_app.py`.
3. Before (or right after) deploying, open **Advanced settings → Secrets** and paste:

   ```toml
   APP_PASSWORD = "choose-a-password-to-share-with-your-network"
   ANTHROPIC_API_KEY = "sk-ant-your-real-key"
   DAILY_LLM_QUOTA = 15
   ```

   (`.streamlit/secrets.toml.example` in this repo shows the same template —
   never commit a real `secrets.toml`, it's gitignored for exactly that
   reason.)
4. Click **Deploy**. You'll get a `https://<something>.streamlit.app` URL —
   that's what you share with your network.

### 3. Share it

Send people the URL + the password you set in step 3. That's the whole
access control — no per-user accounts.

## Cost & abuse protection

You picked "my key, shared by everyone" — meaning every LLM-based analysis
anyone runs is billed to the `ANTHROPIC_API_KEY` above. Two things bound
that cost:

- **Fixed per-run cost**: LLM theme discovery always samples ~180 reviews
  regardless of how many total reviews someone requests (100 or 1000 cost
  the same one Claude call).
- **`DAILY_LLM_QUOTA`**: a soft cap (default 15) on LLM-enabled analyses per
  day, shared across everyone using the link. Once hit, further requests
  that day automatically fall back to the free offline discovery instead of
  failing — the app stays usable, it just stops spending API credits until
  the counter resets at midnight UTC.

This quota is tracked in a file on the app's own filesystem, so it resets
whenever Streamlit Cloud restarts/redeploys the app (occasional, not
frequent) — a soft protection appropriate for sharing with your network, not
a hard guarantee for a large public audience. Raise or lower
`DAILY_LLM_QUOTA` in the app's Secrets settings any time, no redeploy code
change needed (the app picks it up on next run).

## Local testing before deploying

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # fill in real values
streamlit run streamlit_app.py
```

`.streamlit/secrets.toml` is gitignored — it's only for local runs; the
deployed app reads secrets from Streamlit Cloud's own settings instead.

## Updating the deployed app

Push to `main` on GitHub — Streamlit Community Cloud auto-redeploys on every
push. No manual redeploy step.
