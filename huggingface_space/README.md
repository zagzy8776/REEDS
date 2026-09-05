---
title: LOYAL EDGE Model Trainer
emoji: 🏆
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 5.33.0
app_file: app.py
pinned: true
license: mit
---

# LOYAL EDGE — Model Trainer Space

This Hugging Face Space trains all REEDS sport prediction models (soccer, basketball, tennis, NFL, NHL, cricket, rugby, baseball) and uploads them to your Render backend.

## Setup

Add the following **Repository Secrets** in your Space Settings → Repository secrets:

| Secret | Value |
|---|---|
| `DATABASE_URL` | Your Neon Postgres connection string |
| `ADMIN_API_KEY` | Your Render admin key |
| `RENDER_URL` | `https://reeds-phj1.onrender.com` |
| `CRON_SECRET` | Optional secret used to authenticate Render wake requests |

## Usage

Open the Space UI and use the buttons to:
- **Check DB** — see how many rows exist per sport
- **Train All Sports** — trains all models and uploads to Render
- **Ingest Free Data** — downloads historical data from free sources first
- **Start Auto-Polling** — keeps the Space alive, auto-retrains when Render signals new data

The worker also installs the production leakage-safe chronological ensemble before training so HF, Render, and GitHub Actions use the same model-quality path.
