# AlgoTrade — Setup on a New System

Three approaches in order of ease: **Docker** (recommended), **Manual**, **Git clone from scratch**.

---

## Prerequisites (all approaches)

| Tool | Minimum version | Check |
|------|----------------|-------|
| Git | any | `git --version` |
| Python | 3.10+ | `python --version` |
| Node.js | 18+ | `node --version` |
| Docker Desktop | 4+ | `docker --version` (Docker approach only) |

---

## Approach 1 — Docker (fastest, zero config)

### Step 1 — Get the code onto the new machine

**Option A: Clone from GitHub (cleanest)**
```bash
git clone https://github.com/manujagupta12/kite_source.git
cd kite_source
```

**Option B: Copy the folder** (if no internet on target machine)
- Zip `C:\AlgoTrading\kite_source`, transfer via USB/network
- Exclude `node_modules/`, `__pycache__/`, `data/`, `.env` before zipping
- Unzip on the target machine

### Step 2 — Create .env
```bash
cp .env.example .env
```
Edit `.env` and fill in at minimum:
```env
SECRET_KEY=your-strong-secret-here   # change from default!
DHAN_CLIENT_ID=                       # optional, enables live WebSocket ticks
DHAN_ACCESS_TOKEN=                    # optional
DEMO_MODE=false
```

### Step 3 — Create data directory
```bash
mkdir data
```

### Step 4 — Launch
```bash
# Dev (local machine)
docker compose up --build

# Production (background, resource limits)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### Access
| Service | URL |
|---------|-----|
| Dashboard | http://localhost:80 |
| Backend API | http://localhost:8000 |
| Health check | http://localhost:8000/health |
| Signal audit | http://localhost:8000/signals/audit |

### Stop / restart
```bash
docker compose down          # stop
docker compose down -v       # stop + wipe volumes
docker compose restart       # restart both services
docker compose logs -f       # tail logs
```

---

## Approach 2 — Manual (no Docker)

### Step 1 — Get the code
Same as Approach 1 Step 1 (clone or copy).

### Step 2 — Backend
```bash
cd app/backend

# Create virtualenv
python -m venv venv

# Activate
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install deps
pip install -r requirements.txt

# Create .env in project root
cp ../../.env.example ../../.env
# Edit ../../.env and fill in SECRET_KEY at minimum

# Create data dir
mkdir C:\data        # Windows (hard-coded in backend)
# OR on Linux:
mkdir -p /data
```

### Step 3 — Start backend
```bash
# From project root, with venv active:
uvicorn app.backend.main:app --host 0.0.0.0 --port 8000 --reload
```
Or use the batch file on Windows:
```
restart_backend.bat
```

### Step 4 — Frontend
```bash
cd app/frontend
npm install
npm run dev          # dev server on port 5173
```

### Step 5 — Access
| Service | URL |
|---------|-----|
| Dashboard (dev) | http://localhost:5173 |
| Backend API | http://localhost:8000 |

> **Note:** In dev mode Vite proxies `/api` to port 8000 automatically.  
> Check `vite.config.js` if the proxy isn't working.

---

## Approach 3 — Just the backend (no frontend)

Useful for a headless server or if you only need signals via API.

```bash
git clone https://github.com/manujagupta12/kite_source.git
cd kite_source
python -m venv venv && venv\Scripts\activate    # Windows
pip install -r app/backend/requirements.txt
cp .env.example .env   # edit .env
mkdir C:\data
uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
```

Consume signals via:
- `GET /signals` — all live signals
- `GET /signals/audit` — strategy health
- `GET /signals/gold` — gold signals
- `GET /health` — uptime check

---

## Environment Variables Reference

| Variable | Required | Notes |
|----------|----------|-------|
| `SECRET_KEY` | **YES** | Change from default before any deployment |
| `DHAN_CLIENT_ID` | No | Enables sub-second WebSocket ticks |
| `DHAN_ACCESS_TOKEN` | No | Rotates daily — update in `.env` each morning |
| `DEMO_MODE` | No | `true` allows mock signals; `false` (default) = production |
| `BROKER` | No | `dhan | kite | upstox | paper`; auto-detected if blank |
| `UPSTOX_API_KEY` | No | Only if using Upstox |
| `UPSTOX_API_SECRET` | No | Only if using Upstox |
| `VITE_API_URL` | No | Set when frontend and backend are on different hosts |
| `VITE_WS_URL` | No | Set when deploying to a remote server |

---

## Data Directory

The backend reads/writes to `/data` on Linux or `C:\data` on Windows.

```
/data (or C:\data)
├── signals_YYYY-MM-DD.json   # auto-created: today's signal history
├── dhan_token.json           # auto-created: Dhan auth token cache
└── trade_logs/               # auto-created: paper trade records
```

On Docker, `./data` in the project root is mounted into the container — no action needed.  
On manual installs, the directory must exist before starting the backend.

---

## Windows-specific Bat Files

```
start.bat                  — start backend + frontend
restart_backend.bat        — kill old uvicorn, restart fresh
launch_with_commit.bat     — git commit all changes + start both
```

Run from the project root in a normal Command Prompt (not Admin needed).

---

## Verify It's Working

1. Open http://localhost:8000/health → should return `{"status":"ok"}`
2. Open http://localhost:8000/signals/audit → shows per-strategy health
3. Open the dashboard → signals should appear within 3–5 seconds

If signals show "NSE feed down", the NSE scraper failed its first request. Wait 30 seconds — the circuit breaker auto-recovers.

---

## Common Issues

| Symptom | Fix |
|---------|-----|
| `Port 8000 already in use` | `taskkill /f /im python.exe` on Windows, or `lsof -ti:8000 \| xargs kill` on Linux |
| `ModuleNotFoundError: algo` | Run `uvicorn` from the project root, not from `app/backend/` |
| `C:\data does not exist` | `mkdir C:\data` once; the backend can't create the root drive path |
| `DHAN_ACCESS_TOKEN expired` | Update `.env` with today's token from web.dhan.co → Profile → API tokens |
| Docker `build failed on npm install` | Delete `app/node_modules/` before building — it's from a different platform |
| Frontend blank page | Check `vite.config.js` proxy target matches your backend port |

---

## Security Checklist Before Running on Any Non-Local Machine

- [ ] `SECRET_KEY` changed from default
- [ ] `.env` is NOT committed to git (check `git status`)
- [ ] `DHAN_ACCESS_TOKEN` rotated (previous token was exposed in git history)
- [ ] Backend port 8000 is firewalled if on a public network
- [ ] `DEMO_MODE=false` in production
