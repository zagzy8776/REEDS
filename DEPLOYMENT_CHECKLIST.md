# REEDS Deployment Checklist

## Pre-Deployment

### Environment Setup
- [ ] Set `DATABASE_URL` in Render (Neon PostgreSQL)
- [ ] Set `ADMIN_API_KEY` in Render (your secure key: 23235567Jjmt)
- [ ] Set `AUDIT_MODE=true` for initial 7-day testing period
- [ ] Set `ENABLE_SCHEDULER=true`
- [ ] Set `CORS_ORIGINS=https://reeds-phi.vercel.app`
- [ ] Set any API keys (`API_FOOTBALL_KEY`, `THE_ODDS_API_KEY`, etc.)

### Database Verification
- [ ] Run `python backend/scripts/create_drift_tables.py` to create monitoring tables
- [ ] Verify connection: `python -c "from app.db.session import SessionLocal; db = SessionLocal(); print('Connected'); db.close()"`
- [ ] Check existing data: Verify fixtures and predictions tables have data

### Model Testing
- [ ] Run `python backend/test_mathematical_models.py` to verify all models work
- [ ] Run `python backend/test_prediction_gen.py` to test prediction generation

## Deployment Day

### Code Push
- [ ] Commit all changes: `git add . && git commit -m "Production release with mathematical models"`
- [ ] Push to GitHub: `git push origin main`
- [ ] Verify Render deployment starts automatically

### Post-Deployment Verification
- [ ] Check Render logs for errors
- [ ] Visit `https://reeds-phj1.onrender.com/api/public/predictions/today` to verify API works
- [ ] Visit `https://reeds-phj1.onrender.com/api/public/fixtures/status` to check data
- [ ] Verify frontend at `https://reeds-phi.vercel.app` loads correctly

### Monitoring Setup
- [ ] Set up Render alerts for crashes
- [ ] Create daily reminder to check drift reports
- [ ] Bookmark admin endpoints for monitoring

## Daily Audit Mode Tasks (Days 1-7)

### Each Day
- [ ] Check prediction volume: `/api/public/predictions/today`
- [ ] Review drift status: Check `drift_check_logs` table
- [ ] Monitor system health: Check Render logs
- [ ] Record daily metrics in spreadsheet:
  - Predictions generated
  - Hit rate (simulated)
  - ROI (simulated)
  - Any errors/warnings

### After 7 Days
- [ ] Analyze full week of audit data
- [ ] Calculate overall hit rate and ROI
- [ ] Check for drift warnings
- [ ] Decide: Proceed to live or continue tuning

## Go-Live Checklist

### Before Enabling Live Betting
- [ ] Verify 7-day audit showed >55% hit rate on +EV bets
- [ ] Verify positive simulated ROI
- [ ] No critical drift warnings
- [ ] Set `AUDIT_MODE=false`
- [ ] Start with minimal stakes (0.25% bankroll)

### First 24 Hours Live
- [ ] Monitor every prediction
- [ ] Verify bets are being placed correctly
- [ ] Check bankroll manager constraints
- [ ] Watch for any technical issues

### After 3 Days Live
- [ ] If stable, increase to 0.5% bankroll
- [ ] Continue monitoring performance

### After 7 Days Live
- [ ] If performance good, enable full Kelly (25% fraction)
- [ ] Implement ongoing monitoring routine

## Emergency Procedures

### If System Crashes
1. Check Render logs for error details
2. Restart deployment if needed
3. Verify database connection
4. Check if scheduler is running

### If Performance Danks
1. Run drift analysis: Check `drift_check_logs`
2. Review recent predictions for patterns
3. Consider reducing stakes
4. If hit rate <50% for 3 days, pause betting

### If Drawdown >20%
1. Stop all betting immediately
2. Analyze root cause
3. Retrain models if needed
4. Restart with minimal stakes

## Ongoing Maintenance

### Weekly
- [ ] Review performance by market
- [ ] Check for data drift
- [ ] Analyze edge decay
- [ ] Adjust parameters if needed

### Monthly
- [ ] Full backtest on accumulated data
- [ ] Review bankroll growth
- [ ] Assess model accuracy
- [ ] Plan improvements

## Contact Information

- **GitHub**: https://github.com/zagzy8776/REEDS
- **Render Dashboard**: https://dashboard.render.com
- **Vercel Dashboard**: https://vercel.com/dashboard

---

**Remember**: The 7-day audit period is critical. Do not skip it. Better to miss a week of betting than to lose money on an unvalidated system.