# AlgoTrade — Deployment Guide

## Option A — Cloud (access from any device, free)

### Render.com — 5 minute setup, no credit card

1. Go to **https://dashboard.render.com** → sign up free
2. Click **New → Blueprint**
3. Connect your GitHub account → select `manujagupta12/kite_source`
4. Render auto-detects `render.yaml` → click **Apply**
5. In the backend service → **Environment** tab → add:
   - `DHAN_CLIENT_ID` = `1111872026`
   - `DHAN_ACCESS_TOKEN` = your token from web.dhan.co
6. Wait ~3 minutes for build → open the frontend URL

**Result:** Dashboard live at `https://algotrade-frontend.onrender.com`  
Access from phone, tablet, any device, anywhere.

> Free tier sleeps after 15min inactivity — first load takes ~30s to wake up.  
> To keep it always awake, add a free uptime monitor at https://uptimerobot.com

---

## Option B — Local (single click, your Windows PC)

### First time only — install once
- Python 3.10+ from https://python.org *(check "Add to PATH")*
- Node.js 18+ from https://nodejs.org

### Every day — double-click `start.bat`
That's it. Opens dashboard at http://localhost:5173 automatically.

For live algo signals → double-click `profitmachine.bat` instead.

### Access from other devices on same WiFi
Your machine's local IP (e.g. `http://192.168.1.5:5173`) works on any  
phone/tablet connected to the same network. Run `ipconfig` to find your IP.

---

## Option C — Docker (any machine with Docker installed)

```bash
docker compose up --build
# Opens at http://localhost
```

With Dhan token:
```bash
DHAN_CLIENT_ID=1111872026 DHAN_ACCESS_TOKEN=your_token docker compose up --build
```

---

## Login
```
Email:    demo@algotrade.in
Password: demo123
```
