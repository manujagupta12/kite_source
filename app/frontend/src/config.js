// ── API / WS URL resolution ────────────────────────────────────────────────────────────────────
//
//  Dev (npm run dev):
//    vite.config.js proxies /api/* → http://localhost:8000/*
//    vite.config.js proxies /ws/*  → ws://localhost:8000/*
//    So frontend just uses relative paths: /api/... and /ws/...
//
//  Docker / Production:
//    nginx proxies /api/* → backend:8000
//    nginx proxies /ws/*  → backend:8000
//    Same relative paths work unchanged.
//
//  Override: set VITE_API_URL in .env to point at a remote backend.
//    e.g.  VITE_API_URL=https://api.yourdomain.com
//          VITE_WS_URL=wss://api.yourdomain.com
//
const _explicitApi = import.meta.env?.VITE_API_URL || "";
const _explicitWs  = import.meta.env?.VITE_WS_URL  || "";

// In production/docker both are empty → relative paths used.
// In dev both are empty too → relative paths, proxied by Vite.
// Only when VITE_API_URL is explicitly set do we use an absolute URL.
export const API     = _explicitApi;   // "" means use relative path
export const WS_PATH = _explicitWs     // absolute ws URL if set
  || (typeof window !== "undefined"
      ? window.location.origin.replace(/^http/, "ws")
      : "ws://localhost:8000");

// WS endpoint — always absolute because WebSocket API requires it
// Dev:  ws://localhost:5173/ws/signals  → proxied by Vite → ws://localhost:8000/ws/signals
// Prod: wss://yourdomain.com/ws/signals → proxied by nginx
export const WS = (typeof window !== "undefined"
  ? window.location.origin.replace(/^http/, "ws")
  : "ws://localhost:8000") + "/ws/signals";
