# Credential rotation runbook — REEDS

SECURITY NOTICE: a live Neon PostgreSQL connection string (including its
password) was found committed to this repository. Git history still contains
it, so it must be treated as PUBLIC and must never be reused. This document
lists what to rotate, where the exposure lives, and where the replacement
secrets belong. **No secret values appear in this file** — reference
credentials by name/location only.

IMPORTANT: repository scrubbing (this change set) does NOT un-leak history.
Rotation below is mandatory and manual.

WHAT WAS EXPOSED
================

1. Neon PostgreSQL connection string (database owner role, pooler host)
   - Where it was committed:
     * `backend/test_prediction_gen.py` — hardcoded as `os.environ['DATABASE_URL'] = '...'`
       (scrubbed in this change set; the script now requires the env var).
     * Git history: present in past revisions of that file — rotation is the
       only real fix.
   - Posture: treat as compromised. Rotate the Neon password or delete the
     branch/project after migration targets are verified.

2. Nothing else was found with a literal value. Explicitly checked for:
   AWS access keys (AKIA…), GitHub tokens (ghp_/github_pat_), OpenAI-style
   keys (sk-…), Slack tokens (xox…), `ADMIN_API_KEY=`, `GITHUB_TOKEN=` with
   real values. All matches were documented placeholders
   (e.g. `.env.example`, Colab notebook `CHANGE_ME`/`ep-xxx`).

3. Non-secret but sensitive references that remain intentional:
   - Production Render URL and Vercel origin (identifiers, not secrets).

ROTATION CHECKLIST (manual — do not automate)
=============================================

[ ] 1. Neon: rotate the database password (or reset credentials) for the
       exposed role. Prefer creating a NEW password and updating the secret
       stores; do not reuse the old value anywhere.
[ ] 2. After Phase 2/3 migration verification, consider deleting the exposed
       Neon branch/project entirely instead of rotating in place.
[ ] 3. ADMIN_API_KEY (Render + Kaggle secrets + EC2 /etc/reeds.env): rotate
       as a precaution since it authenticated the same production service the
       leaked URL pointed at. Update all consumers in one coordinated change:
       Render env, Kaggle User Secrets, EC2 env file.
[ ] 4. GITHUB_TOKEN (EC2 /etc/reeds.env + Render secret): rotate if it has
       write access to the repo (model release publishing). Scope a new PAT to
       Contents: read/write only.
[ ] 5. Provider API keys (API_FOOTBALL_KEY, API_SPORTS_KEY, SPORTMONKS_API_KEY,
       FOOTBALL_DATA_API_KEY, API_BASKETBALL_KEY, ALLSPORTSAPI_KEY,
       THE_ODDS_API_KEY, BZZOIRO_API_KEY, OPENFOOT_API_KEY): no evidence of
       exposure in this repo — rotate only if your logs show misuse.
[ ] 6. Cron secret (CRON_SECRET): no evidence of exposure; rotate on schedule.

WHERE SECRETS BELONG AFTER ROTATION
===================================

- Render dashboard env vars: DATABASE_URL (legacy/Neon today; later
  AIVEN_DATABASE_URL), ADMIN_API_KEY, CRON_SECRET, GITHUB_TOKEN, provider keys.
- Kaggle User Secrets: DATABASE_URL, ADMIN_API_KEY, RENDER_URL (names only;
  values entered in the Kaggle UI).
- EC2: /etc/reeds.env (root-owned, mode 600) referenced by
  deploy/ec2/reeds-train.service EnvironmentFile.
- Local development: a git-ignored `.env` / `.env.local`
  (root .gitignore now covers `.env.*` at any depth).

DONE CRITERIA
=============

- All rotated values live only in the stores above.
- `git grep` for the old values returns nothing in tracked files.
- Rotation dates and operator initials recorded here after each step.
