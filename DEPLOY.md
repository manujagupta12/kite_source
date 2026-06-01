# AlgoTrade — Deployment Guide

## Option A — Cloud: Railway (backend) + Vercel (frontend)
**Free, no credit card needed**

### Step 1 — Deploy backend on Railway

1. Go to **https://railway.app** → Sign in with GitHub (free)
2. Click **New Project → Deploy from GitHub repo**
3. Select `manujagupta12/kite_source`
4. Railway auto-detects `railway.json` → click **Deploy**
5. Once deployed, go to **Settings → Networking → Generate Domain**
6. Copy the domain e.g. `algotrade-backend.up.railway.app`
7. Go to **Variables** tab → add:
   - `DHAN_CLIENT_ID` = `1111872026`
   - `DHAN_ACCESS_TOKEN` = your token from web.dhan.co
   - `SECRET_KEY` = any long random string

### Step 2 — Deploy frontend on Vercel

1. Go to **https://vercel.com** → Sign in with GitHub (free)
2. Click **Add New → Project** → select `manujagupta12/kite_source`
3. Set **Root Directory** to `app/frontend`
4. Under **Environment Variables** add:
   - `VITE_API_URL` = `https://algotrade-backend.up.railway.app`
   - `VITE_WS_URL`  = `wss://algotrade-backend.up.railway.app`
5. Click **Deploy**
6. Vercel gives you a URL like `https://kite-source.vercel.app`

Open that URL on **any device, anywhere** — phone, tablet, laptop.

---

## Option B — Instant access via Ngrok (your PC becomes the server)
No cloud, no signup needed.

1. Download ngrok: **https://ngrok.com/download**
2. Double-click `start.bat` to start the platform locally
3. Open a new terminal and run:
   ```
   ngrok http 5173
   ```
4. Ngrok shows a public URL like `https://abc123.ngrok.io`
5. Open that URL on any device

> URL changes every time you restart ngrok on free tier.

---

## Option C — Local (same WiFi only)
1. Double-click `start.bat`
2. Find your PC IP: run `ipconfig` → look for `IPv4 Address`
3. Open `http://YOUR_IP:5173` on any device on same WiFi

---

## Option D — Docker (any machine with Docker)
```bash
docker compose up --build
# Open http://localhost
```

---

## Login
```
Email:    demo@algotrade.in
Password: demo123
```
