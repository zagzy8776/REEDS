# REEDS Deployment Checklist

## Pre-Deployment

### Environment Setup
- [ ] Set `DATABASE_URL` in Render (Neon PostgreSQL)
- [ ] Set `ADMIN_API_KEY` in Render using the secret manager (never commit it)
- [ ] Set `CRON_SECRET` in Render and the Hugging Face Space; keep both values identical when possible
- [ ] Set `AUDIT_MODE=true` for initial 7-day testing period
- [ ] Set `ENABLE_SCHEDULER=true`
- [ ] Set `CORS_ORIGINS=https://reeds-phi.vercel.app`
- [ ] Set required API keys in Render secrets

### Database Verification
- [ ] Run `python backend/scripts/create_drift_tables.py` to create monitoring tables
- [ ] Verify PostgreSQL connectivity
- [ ] Check fixtures and predictions tables contain data

### Model Testing
- [ ] Run `python backend/test_mathematical_models.py`
- [ ] Run `backend/test_prediction_gen.py`

## Deployment Day

### Code Push
- [ ] Push changes to `main`
- [ ] Verify Render deploys automatically from `main`
- [ ] Hugging Face worker must pull the latest `huggingface_space/app.py` directly from `main`; GitHub Actions is not required

### Post-Deployment Verification
- [ ] Render health check uses `/ready`
- [ ] Visit the public prediction endpoint
- [ ] Visit the fixture status endpoint
- [ ] Verify the Hugging Face Space starts and its auto-poll loop reports Render status
- [ ] Use the Space's Wake Render button and confirm `/api/wake` returns HTTP 200
- [ ] Confirm the wake response reports `cron_secret` or `admin_api_key_fallback` authentication

### Monitoring Setup
- [ ] Set up Render crash/deploy alerts
- [ ] Monitor `/health` and `/ready`
- [ ] Monitor HF worker logs for poll/wake failures

## Security

- Never store `ADMIN_API_KEY`, `CRON_SECRET`, database passwords, provider API keys, or GitHub tokens in tracked documentation.
- After any credential is exposed in source control or chat, rotate it before production use.
- The HF worker must never print secret values; diagnostics may expose only configured/length/hash metadata.

## Go-Live Controls

- Keep model and prediction quality in audit mode until the agreed validation window is complete.
- Do not treat model accuracy alone as evidence of profitability.

## Emergency Procedures

### If Render Crashes
1. Check Render logs and the latest deployment.
2. Verify `/ready` and PostgreSQL connectivity.
3. Confirm `ENABLE_SCHEDULER=true` only when the service is healthy.
4. Redeploy `main` if the image/runtime is corrupted.

### If HF Worker Cannot Reach Render
1. Check the Space's `RENDER_URL`.
2. Confirm `ADMIN_API_KEY` works against the Render admin surface.
3. `CRON_SECRET` is preferred for `/api/wake`; the trusted HF worker can fall back to `X-Admin-Key` if the cron secret has drifted.
4. Use the Space diagnostic output; it compares only non-secret hashes/lengths.
5. Verify Render `/api/wake` logs show HTTP 200 rather than 401/5xx.

## Ongoing Maintenance

### Weekly
- Review model performance and data drift.
- Check provider coverage and failed sources.
- Verify HF worker synchronization from the public GitHub source.

### Monthly
- Full backtest on accumulated data.
- Review model registry and active versions.
- Rotate credentials where operational policy requires it.
