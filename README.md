<div align="center">

# 📈 AlgoTrade — NSE F&O Signal Platform

**Version 1.1.0** — Production-ready, fully containerised

[![CI/CD](https://github.com/manujagupta12/source/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/manujagupta12/source/actions/workflows/ci-cd.yml)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://github.com/manujagupta12/source/pkgs/container/algotrade-backend)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> A real-time NSE F&O signal dashboard covering every major India options trading strategy.
> Ships as a **single `docker compose up` command** and runs globally on any cloud.

</div>

---

## ✨ Version 1.1.0 — What’s New

| Feature | Details |
|---|---|
| **Trade Logger** | Full `trader_logger.py` integration — CSV-backed trade log with enter/close/export |
| **Signal Charts** | Price area chart on every signal card (NIFTY, BANKNIFTY, FINNIFTY, Equity) with Entry/Target/SL overlays |
| **PCR OI Chart** | 3-line chart: Call OI ➔ Put OI ➔ Index Spot per instrument |
| **Subscription tiers** | Weekly ₹500 / Monthly ₹1,500 / Annual ₹10,000 with billing-cycle toggle |
| **Log Trade button** | One-click from any signal card → pre-fills the trade logger form |

---

## 📊 F&O + Equity Strategies

| ID | Strategy | Type | Instruments |
|----|----------|------|-------------|
| S1 | Calendar Spread | Neutral time-decay | NIFTY, BANKNIFTY, FINNIFTY |
| S2 | Iron Condor | Neutral range-bound | NIFTY, BANKNIFTY, FINNIFTY |
| S3 | Short Straddle | High-IV crush | NIFTY, BANKNIFTY, FINNIFTY |
| S4 | 0DTE Scalp | Expiry momentum | NIFTY, BANKNIFTY, FINNIFTY |
| S5 | **PCR Contrarian** 🆕 | OI-based reversal | NIFTY, BANKNIFTY, FINNIFTY |
| E1 | EMA Crossover | Momentum | NSE Equity |
| E2 | VWAP Reversion | Mean-reversion | NSE Equity |
| E3 | ORB Breakout | Opening Range | NSE Equity |
| E4 | Gap Fill | Overnight gap | NSE Equity |

---

## 🚀 Quick Start (Docker)

```bash
git clone https://github.com/manujagupta12/source.git algotrade && cd algotrade
cp .env.example .env
docker compose up --build
# Open http://localhost  →  demo@algotrade.in / demo123
```

---

## 🌐 Global Production Deployment (Cloud VPS)

This platform is designed to run **24/7 on a cloud server** accessible from anywhere.

### Option A — Any VPS (DigitalOcean / AWS / GCP / Hetzner)

```bash
# 1. Provision Ubuntu 22.04 VPS (min 2 vCPU, 2GB RAM)
# 2. Install Docker
curl -fsSL https://get.docker.com | sh

# 3. Clone & configure
git clone https://github.com/manujagupta12/source.git algotrade && cd algotrade
cp .env.example .env

# 4. Set your domain and production secret
echo "SECRET_KEY=$(openssl rand -hex 32)" >> .env
echo "VITE_API_URL=https://api.yourdomain.com" >> .env
echo "VITE_WS_URL=wss://api.yourdomain.com" >> .env

# 5. Launch with production config
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 6. Verify
curl http://localhost:8000/health
```

### Option B — Domain + HTTPS (nginx + Certbot)

```bash
# Install nginx + certbot on host
apt install -y nginx certbot python3-certbot-nginx

# Get SSL certificate
certbot --nginx -d yourdomain.com -d api.yourdomain.com

# nginx reverse proxy config: /etc/nginx/sites-available/algotrade
```

```nginx
# Frontend (yourdomain.com)
server {
    listen 443 ssl;
    server_name yourdomain.com;
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:80;
    }
}

# API (api.yourdomain.com)
server {
    listen 443 ssl;
    server_name api.yourdomain.com;
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

```bash
nginx -t && systemctl reload nginx
# Auto-renew SSL
(crontab -l; echo "0 3 * * * certbot renew --quiet") | crontab -
```

### Option C — Fly.io (Free tier, global edge)

```bash
curl -L https://fly.io/install.sh | sh
fly auth login
fly launch --name algotrade-backend  # follow prompts, select region
fly launch --name algotrade-frontend
fly deploy
```

### Option D — Railway.app (One-click)

1. Fork this repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select `manujagupta12/source`
4. Set env vars: `SECRET_KEY`, `PORT=8000`
5. Railway auto-assigns a public URL

---

## 💳 Subscription Plans

| Plan | Weekly | Monthly | Annual |
|------|--------|---------|--------|
| **Free** | ₹0 | ₹0 | ₹0 |
| **Weekly** | **₹500/week** | — | — |
| **Monthly** | — | **₹1,500/month** | — |
| **Annual** | — | — | **₹10,000/year** |

Annual plan saves ₹8,000 vs monthly billing.

---

## 🔄 CI/CD Pipeline

```
Push to main
    ├─► [1] Lint & Test (flake8 + Vite build)
    ├─► [2] Build & Push Docker Images to GHCR (amd64 + arm64)
    └─► [3] GitHub Release (on semver tag v*.*.*)
```

```bash
# Release v1.1.0
git tag v1.1.0 && git push origin v1.1.0
```

---

## 📁 Project Structure

```
algotrade/
├── Dockerfile.backend        # Python 3.11-slim — FastAPI
├── Dockerfile.frontend       # Node 20 build → nginx:alpine
├── docker-compose.yml        # Dev: one-command launch
├── docker-compose.prod.yml   # Prod: resource limits + logging
├── nginx.conf                # SPA fallback + API/WS proxy
├── .github/workflows/
│   └── ci-cd.yml              # Full CI/CD pipeline
├── algo/
│   ├── pcr_strategy.py        # S5 PCR Contrarian (live OI)
│   ├── Calendaralgofinal.py   # S1 Calendar Spread
│   ├── trader_logger.py       # CSV trade logger (NEW)
│   ├── multistrategy.py       # S2–S4 engines
│   └── nse_connector.py       # NSE API client
└── app/
    ├── backend/main.py        # FastAPI v3.2.0
    └── frontend/src/App.jsx   # React dashboard v1.1.0
```

---

## 🛑 Disclaimer

> For **educational and research purposes only**. Not SEBI-registered investment advice.
> Always consult a qualified financial advisor before trading.

---

<div align="center">

Built with ❤️ for India’s F&O traders — **v1.1.0** — May 2026

</div>
