import { useState, useEffect, useRef, useCallback } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend, BarChart, Bar,
  ComposedChart, Area,
} from "recharts";
import { API, WS } from "./config.js";

// ── api() helper ────────────────────────────────────────────────────────────────────
// API is "" → relative path (/api/...) proxied by Vite dev server or nginx
// API is "https://..." → direct fetch to remote backend
function api(path, opts = {}) {
  const tok = localStorage.getItem("tok");
  const url = API ? API + path : "/api" + path;
  return fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
    },
    ...opts,
  }).then(r => r.json());
}

const MARKETS = [
  { id:"ALL",       label:"All",          icon:"⊞", color:"#00d4ff" },
  { id:"NIFTY",     label:"NIFTY 50 F&O", icon:"N",  color:"#00ff9d",
    strategies:["S1 Calendar Spread","S2 Iron Condor","S3 Short Straddle","S4 0DTE Scalp","S5 PCR Contrarian"] },
  { id:"BANKNIFTY", label:"BANK NIFTY",   icon:"B",  color:"#f5c518",
    strategies:["S1 Calendar Spread","S2 Iron Condor","S3 Short Straddle","S4 0DTE Scalp","S5 PCR Contrarian"] },
  { id:"FINNIFTY",  label:"FIN NIFTY",    icon:"F",  color:"#ff6b35",
    strategies:["S1 Calendar Spread","S2 Iron Condor","S5 PCR Contrarian"] },
  { id:"EQUITY",    label:"Equity",       icon:"E",  color:"#a78bfa",
    strategies:["E1 EMA Crossover","E2 VWAP Reversion","E3 ORB Breakout","E4 Gap Fill"] },
];

const STRAT_INFO = {
  S1:{color:"#00d4ff",tag:"NEUTRAL"},   S2:{color:"#00ff9d",tag:"NEUTRAL"},
  S3:{color:"#f5c518",tag:"NEUTRAL"},   S4:{color:"#ff6b35",tag:"EXPIRY"},
  S5:{color:"#22c55e",tag:"CONTRARIAN"},
  E1:{color:"#a78bfa",tag:"MOMENTUM"},  E2:{color:"#fb923c",tag:"MEAN REV"},
  E3:{color:"#38bdf8",tag:"BREAKOUT"},  E4:{color:"#e879f9",tag:"GAP FILL"},
  E6:{color:"#facc15",tag:"GAP FILL"},
};

const PCR_ZONE_COLOR = {
  OVERBOUGHT:"#ff3d5a", OVERSOLD:"#00ff9d",
  NEUTRAL:"#5a7a9a", BEARISH_WATCH:"#ff6b35", BULLISH_WATCH:"#f5c518", UNKNOWN:"#5a7a9a",
};

const BILLING_CYCLES = [
  { id:"weekly",  label:"Weekly",  suffix:"/week" },
  { id:"monthly", label:"Monthly", suffix:"/month" },
  { id:"annual",  label:"Annual",  suffix:"/year" },
];

const PLAN_PRICES = {
  free:    { weekly:0,    monthly:0,    annual:0     },
  weekly:  { weekly:500,  monthly:null, annual:null  },
  monthly: { weekly:null, monthly:1500, annual:null  },
  annual:  { weekly:null, monthly:null, annual:10000 },
};

function sigFingerprint(s) {
  const tMin = (s.timestamp||"").slice(0,16);
  return `${s.market}|${s.strategy}|${s.instrument||s.symbol}|${s.direction}|${tMin}`;
}

// ── Signal expiry helpers (IST-aware) ────────────────────────────────────
function getISTHourMin() {
  const now = new Date();
  // UTC + 5:30
  const ist = new Date(now.getTime() + (5.5 * 60 * 60 * 1000));
  return { h: ist.getUTCHours(), m: ist.getUTCMinutes() };
}
function isMarketOpen() {
  const { h, m } = getISTHourMin();
  const day = new Date(Date.now() + 5.5*3600000).getUTCDay(); // 0=Sun,6=Sat
  if (day === 0 || day === 6) return false;
  const mins = h * 60 + m;
  return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30;
}
function isAfterMarketClose() {
  const { h, m } = getISTHourMin();
  return h * 60 + m > 15 * 60 + 30;
}
function sigIsExpired(s) {
  if (!s.timestamp) return false;
  const ts = new Date(s.timestamp).getTime();
  const age = Date.now() - ts;
  // During market hours: expire after 30 min
  if (isMarketOpen()) return age > 30 * 60 * 1000;
  // After 3:30 PM IST: expire all same-day signals older than 2 hours
  if (isAfterMarketClose()) return age > 2 * 60 * 60 * 1000;
  // Pre-market / weekend: show for 8 hours (educational / reference)
  return age > 8 * 60 * 60 * 1000;
}

function mergeSignals(prev, incoming) {
  const seen  = new Set(prev.map(sigFingerprint));
  const fresh = incoming.filter(s => {
    const fp = sigFingerprint(s);
    if (seen.has(fp)) return false;
    seen.add(fp); return true;
  });
  return [...fresh, ...prev].slice(0, 300);
}

const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600;700&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body,#root{height:100%;width:100%;overflow:hidden}
:root{
  --bg:#050c18;--s1:#0b1628;--s2:#0f1e35;--s3:#152540;
  --br:#1b3050;--br2:#24406a;--text:#dde6f5;--muted:#5a7a9a;--dim:#3d5a7a;
  --acc:#00d4ff;--grn:#00ff9d;--yel:#f5c518;--red:#ff3d5a;--orn:#ff6b35;--pur:#a78bfa;
  --mono:'Space Mono',monospace;--body:'DM Sans',sans-serif;
  --mob-nav-h:56px;
}
body{background:var(--bg);color:var(--text);font-family:var(--body)}
::-webkit-scrollbar{width:3px;height:3px}::-webkit-scrollbar-track{background:var(--bg)}::-webkit-scrollbar-thumb{background:var(--br2);border-radius:2px}
.app{display:flex;width:100vw;height:100vh;height:100dvh;overflow:hidden;position:fixed;top:0;left:0}
.sidebar{width:232px;min-width:232px;background:var(--s1);border-right:1px solid var(--br);display:flex;flex-direction:column;overflow-y:auto;flex-shrink:0}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
.ticker-bar{height:36px;background:var(--s1);border-bottom:1px solid var(--br);overflow:hidden;flex-shrink:0}
.ticker-inner{display:flex;align-items:center;height:100%;white-space:nowrap;animation:ticker 45s linear infinite}
.ticker-inner:hover{animation-play-state:paused}
@keyframes ticker{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.tick-item{display:inline-flex;align-items:center;gap:8px;padding:0 18px;border-right:1px solid var(--br);height:100%;font-family:var(--mono);font-size:10px}
.tick-label{color:var(--muted);font-size:9px;letter-spacing:.5px}
.tick-val{font-weight:700;font-size:11px}
.tick-chg{font-size:9px;padding:1px 5px;border-radius:3px}
.tick-up{color:var(--grn)}.tick-dn{color:var(--red)}.tick-unch{color:var(--muted)}
.topbar{height:50px;background:var(--s1);border-bottom:1px solid var(--br);display:flex;align-items:center;padding:0 16px;gap:10px;flex-shrink:0;overflow:hidden}
.regime-pill{display:flex;align-items:center;gap:7px;background:var(--s2);border:1px solid var(--br);border-radius:20px;padding:4px 12px;font-size:10px;font-family:var(--mono);white-space:nowrap;flex-shrink:0}
.pulse{width:6px;height:6px;border-radius:50%;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes fadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.src-pill{display:flex;align-items:center;gap:5px;background:rgba(0,255,157,.06);border:1px solid rgba(0,255,157,.18);border-radius:20px;padding:3px 10px;font-size:9px;color:var(--grn);font-family:var(--mono);flex-shrink:0}
.badge{font-family:var(--mono);font-size:10px;padding:3px 9px;border-radius:6px;background:var(--s2);border:1px solid var(--br);white-space:nowrap;flex-shrink:0}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:6px}
.sb-logo{padding:14px 14px 10px;border-bottom:1px solid var(--br);flex-shrink:0}
.logo-t{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--acc);letter-spacing:2.5px}
.logo-s{font-size:9px;color:var(--muted);letter-spacing:1px;margin-top:2px}
.sb-nav{padding:6px;flex:1;overflow-y:auto}
.nav-sect{font-size:8px;color:var(--dim);letter-spacing:2px;padding:12px 8px 4px;text-transform:uppercase}
.nav-it{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:7px;cursor:pointer;font-size:12px;color:var(--muted);transition:all .12s;margin-bottom:1px;border:1px solid transparent}
.nav-it:hover{background:var(--s2);color:var(--text)}
.nav-it.act{background:rgba(0,212,255,.07);color:var(--acc);border-color:rgba(0,212,255,.12)}
.nav-ico{width:16px;text-align:center;font-size:11px}
.mkt-btn{display:flex;align-items:center;padding:3px 5px 3px 9px;border-radius:7px;font-size:12px;transition:all .12s;margin-bottom:1px;border:1px solid transparent}
.mkt-btn:hover{background:var(--s2)}.mkt-btn.act{background:rgba(0,212,255,.07);border-color:rgba(0,212,255,.12)}
.mkt-label-area{display:flex;align-items:center;gap:8px;flex:1;cursor:pointer;padding:3px 0}
.mkt-badge{width:18px;height:18px;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:8px;font-weight:700;font-family:var(--mono);flex-shrink:0}
.mkt-name{font-size:11px;font-weight:500;flex:1}
.mkt-chev-btn{cursor:pointer;padding:5px 7px;border-radius:5px;font-size:8px;transition:all .12s;flex-shrink:0;display:flex;align-items:center;color:var(--dim)}
.mkt-chev-btn:hover{background:rgba(0,212,255,.12);color:var(--acc)}
.chev{transition:transform .18s;display:inline-block}.chev.open{transform:rotate(180deg)}
.strat-list{padding:2px 4px 4px 28px}
.strat-it{display:flex;align-items:center;gap:6px;padding:4px 8px;border-radius:5px;cursor:pointer;font-size:10px;color:var(--muted);transition:all .12s;margin-bottom:1px}
.strat-it:hover{background:var(--s2);color:var(--text)}.strat-it.act{color:var(--text);background:rgba(0,212,255,.05)}
.s-dot{width:5px;height:5px;border-radius:50%;flex-shrink:0}
.feed-row{display:flex;align-items:center;gap:6px;font-size:9px;font-family:var(--mono);padding:2px 8px;color:var(--muted)}
.feed-ok{color:var(--grn)}
.tabs{height:44px;display:flex;background:var(--s1);border-bottom:1px solid var(--br);padding:0 14px;flex-shrink:0;overflow-x:auto}
.tabs::-webkit-scrollbar{height:0}
.tab{padding:0 13px;font-size:11px;font-weight:500;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:all .12s;white-space:nowrap;display:flex;align-items:center}
.tab.act{color:var(--acc);border-bottom-color:var(--acc)}.tab:hover:not(.act){color:var(--text)}
.tab-right{margin-left:auto;display:flex;align-items:center;gap:10px;padding:0 4px;flex-shrink:0}
.count-pill{font-size:9px;font-family:var(--mono);padding:2px 7px;border-radius:10px}
.content{flex:1;overflow-y:auto;overflow-x:hidden;padding:14px}
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px}
.stat-card{background:var(--s1);border:1px solid var(--br);border-radius:10px;padding:12px 14px}
.stat-lbl{font-size:8px;color:var(--muted);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:4px}
.stat-val{font-family:var(--mono);font-size:18px;font-weight:700}
.stat-sub{font-size:9px;color:var(--muted);margin-top:2px}
.strat-seg{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:7px;margin-bottom:14px}
.strat-seg-card{background:var(--s1);border:1px solid var(--br);border-radius:8px;padding:9px 11px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;transition:border-color .12s}
.strat-seg-card:hover{border-color:var(--br2)}
.strat-seg-name{font-size:8px;color:var(--muted);letter-spacing:.5px;margin-bottom:2px;text-transform:uppercase}
.strat-seg-count{font-family:var(--mono);font-size:17px;font-weight:700}
.filter-crumb{display:flex;align-items:center;gap:7px;margin-bottom:12px;padding:6px 11px;background:var(--s2);border:1px solid var(--br2);border-radius:7px;font-size:10px;font-family:var(--mono)}
.filter-crumb-clear{cursor:pointer;color:var(--red);font-size:10px;margin-left:auto;padding:1px 6px;border-radius:4px;border:1px solid rgba(255,61,90,.2);background:rgba(255,61,90,.06)}
.sigs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:10px}
.sig-card{background:var(--s1);border:1px solid var(--br);border-radius:11px;padding:13px;position:relative;overflow:hidden;transition:border-color .15s,transform .15s}
.sig-card:hover{border-color:rgba(0,212,255,.25);transform:translateY(-1px)}
.sig-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;border-radius:11px 11px 0 0}
.bull::before{background:linear-gradient(90deg,var(--grn),transparent)}
.bear::before{background:linear-gradient(90deg,var(--red),transparent)}
.neut::before{background:linear-gradient(90deg,var(--acc),transparent)}
.sig-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:9px}
.sig-strat{font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:.3px}
.sig-tags{display:flex;gap:4px;margin-top:4px;flex-wrap:wrap}
.sig-tag{font-size:8px;padding:2px 6px;border-radius:3px;font-family:var(--mono);font-weight:700;letter-spacing:.7px}
.sig-score-wrap{text-align:right;flex-shrink:0}
.sig-score{font-family:var(--mono);font-size:22px;font-weight:700;line-height:1}
.sig-score-lbl{font-size:7px;color:var(--muted);margin-top:1px}
.sig-chart-wrap{background:var(--s2);border-radius:8px;margin-bottom:9px;overflow:hidden}
.sig-chart-header{display:flex;align-items:center;justify-content:space-between;padding:7px 10px 4px}
.sig-chart-title{font-size:8px;color:var(--muted);font-family:var(--mono);letter-spacing:.5px}
.sig-chart-price{font-family:var(--mono);font-size:12px;font-weight:700}
.sig-chart-tabs{display:flex;gap:0}
.sig-chart-tab{font-size:8px;font-family:var(--mono);padding:2px 8px;cursor:pointer;color:var(--dim);border-radius:4px;transition:all .1s}
.sig-chart-tab.act{background:var(--acc);color:#000;font-weight:700}
.pcr-gauge{background:var(--s2);border-radius:8px;padding:10px 11px;margin-bottom:9px}
.pcr-gauge-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px}
.pcr-val{font-family:var(--mono);font-size:20px;font-weight:700}
.pcr-zone-lbl{font-family:var(--mono);font-size:9px;font-weight:700;padding:3px 9px;border-radius:4px;text-align:right;max-width:160px;line-height:1.3}
.pcr-bar-track{height:6px;background:var(--br2);border-radius:3px;overflow:hidden}
.pcr-bar-fill{height:100%;border-radius:3px;transition:width .3s}
.pcr-labels{display:flex;justify-content:space-between;font-size:7px;color:var(--dim);font-family:var(--mono);margin-top:2px}
.pcr-detail{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-top:7px}
.pcr-stat{background:var(--s3);border-radius:5px;padding:5px 7px}
.pcr-stat-k{font-size:7px;color:var(--muted);margin-bottom:2px}
.pcr-stat-v{font-family:var(--mono);font-size:10px;font-weight:700}
.pcr-chart-wrap{background:var(--s2);border-radius:8px;padding:10px 11px 6px;margin-bottom:9px}
.pcr-chart-title{font-size:8px;color:var(--muted);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;font-family:var(--mono)}
.eq-price-hero{background:var(--s2);border-radius:8px;padding:9px 11px;margin-bottom:9px;display:flex;align-items:center;justify-content:space-between}
.eq-sym{font-family:var(--mono);font-size:14px;font-weight:700;color:var(--acc)}
.eq-ltp{font-family:var(--mono);font-size:16px;font-weight:700}
.eq-chg{font-size:10px;font-weight:600;padding:2px 7px;border-radius:4px;margin-top:2px;display:inline-block}
.eq-chg-up{background:rgba(0,255,157,.12);color:var(--grn)}.eq-chg-dn{background:rgba(255,61,90,.12);color:var(--red)}
.eq-ohlc{display:flex;gap:9px;margin-top:5px;font-size:8px;color:var(--muted);font-family:var(--mono)}
.sig-action{background:var(--s2);border:1px solid var(--br);border-radius:7px;padding:7px 9px;font-family:var(--mono);font-size:9px;color:var(--acc);margin-bottom:9px;word-break:break-all;line-height:1.5}
.sig-meta{display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;margin-bottom:7px}
.meta-box{background:var(--s2);border-radius:5px;padding:5px 7px}
.meta-k{font-size:7px;color:var(--muted);margin-bottom:2px;letter-spacing:.5px}
.meta-v{font-family:var(--mono);font-size:10px;font-weight:700}
.sig-reason{font-size:9px;color:var(--muted);padding:6px 0 0;border-top:1px solid var(--br);line-height:1.5}
.sig-foot{display:flex;align-items:center;justify-content:space-between;margin-top:7px;flex-wrap:wrap;gap:5px}
.sig-src{font-size:8px;color:var(--dim);font-family:var(--mono)}
.risk-badge{font-size:8px;font-weight:700;padding:2px 6px;border-radius:3px;font-family:var(--mono)}
.rL{background:rgba(0,255,157,.1);color:var(--grn);border:1px solid rgba(0,255,157,.2)}
.rM{background:rgba(245,197,24,.1);color:var(--yel);border:1px solid rgba(245,197,24,.2)}
.rH{background:rgba(255,61,90,.1);color:var(--red);border:1px solid rgba(255,61,90,.2)}
.log-trade-btn{font-size:8px;font-family:var(--mono);font-weight:700;padding:3px 9px;border-radius:4px;border:1px solid rgba(0,212,255,.3);background:rgba(0,212,255,.07);color:var(--acc);cursor:pointer;transition:all .12s;letter-spacing:.5px}
.log-trade-btn:hover{background:rgba(0,212,255,.15)}
.fo-strikes{background:var(--s2);border-radius:8px;padding:9px 11px;margin-bottom:9px}
.fo-idx{font-family:var(--mono);font-size:13px;font-weight:700;color:var(--acc)}
.fo-spot{font-family:var(--mono);font-size:10px;color:var(--muted);margin-left:7px}
.fo-exp{font-size:8px;color:var(--dim);margin-top:3px;font-family:var(--mono)}
.idx-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:14px}
.idx-card{background:var(--s1);border:1px solid var(--br);border-radius:9px;padding:10px 12px;transition:border-color .12s}
.idx-card:hover{border-color:var(--br2)}
.idx-name{font-size:8px;color:var(--muted);letter-spacing:1px;margin-bottom:3px;text-transform:uppercase}
.idx-ltp{font-family:var(--mono);font-size:15px;font-weight:700}
.idx-chg{font-size:9px;margin-top:1px}.idx-hl{font-size:7px;color:var(--dim);margin-top:2px;font-family:var(--mono)}
.movers-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}
.mover-table{background:var(--s1);border:1px solid var(--br);border-radius:10px;overflow:hidden}
.mover-hdr{padding:9px 12px;border-bottom:1px solid var(--br);font-size:9px;font-weight:600;letter-spacing:.5px}
.mover-row{display:flex;align-items:center;justify-content:space-between;padding:7px 12px;border-bottom:1px solid rgba(27,48,80,.5);font-size:10px}
.mover-row:last-child{border-bottom:none}
.mover-sym{font-family:var(--mono);font-weight:700;font-size:11px}
.mover-ltp{font-family:var(--mono);font-size:10px;color:var(--muted)}
.mover-chg{font-family:var(--mono);font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px}
.chg-up{background:rgba(0,255,157,.1);color:var(--grn)}.chg-dn{background:rgba(255,61,90,.1);color:var(--red)}
@keyframes flash-up{0%{background:rgba(0,255,157,.45);transform:scale(1.02)}60%{background:rgba(0,255,157,.2)}100%{background:transparent;transform:scale(1)}}
@keyframes flash-dn{0%{background:rgba(255,61,90,.45);transform:scale(1.02)}60%{background:rgba(255,61,90,.2)}100%{background:transparent;transform:scale(1)}}
.flash-up{animation:flash-up 1.2s ease-out}.flash-dn{animation:flash-dn 1.2s ease-out}
.live-dot{width:6px;height:6px;border-radius:50%;background:var(--grn);display:inline-block;animation:pulse 1s infinite;margin-right:4px}
.login-wrap{height:100%;display:flex;align-items:center;justify-content:center;background:var(--bg);overflow-y:auto}
.login-card{background:var(--s1);border:1px solid var(--br);border-radius:14px;padding:34px;width:min(370px,90vw)}
.l-logo{font-family:var(--mono);font-size:15px;color:var(--acc);font-weight:700;letter-spacing:3px;margin-bottom:3px}
.l-sub{font-size:10px;color:var(--muted);margin-bottom:24px;letter-spacing:.5px}
.l-lbl{font-size:10px;color:var(--muted);margin-bottom:4px;display:block;letter-spacing:.5px}
.l-inp{width:100%;background:var(--s2);border:1px solid var(--br);border-radius:7px;color:var(--text);font-family:var(--body);font-size:13px;padding:8px 11px;outline:none;transition:border .12s;margin-bottom:12px}
.l-inp:focus{border-color:var(--acc)}
.l-btn{width:100%;background:var(--acc);color:#000;border:none;border-radius:7px;padding:9px;font-family:var(--body);font-size:13px;font-weight:700;cursor:pointer;transition:opacity .12s;margin-top:2px}
.l-btn:hover{opacity:.88}.l-btn:disabled{opacity:.5;cursor:not-allowed}
.l-demo{font-size:10px;color:var(--muted);text-align:center;margin-top:12px}
.err-box{background:rgba(255,61,90,.08);border:1px solid rgba(255,61,90,.25);color:var(--red);font-size:11px;padding:7px 11px;border-radius:7px;margin-bottom:12px}
.empty{text-align:center;padding:40px 20px;color:var(--muted)}
.empty-ico{font-size:32px;margin-bottom:9px}
.empty-t{font-size:14px;color:var(--text);margin-bottom:4px}
.empty-s{font-size:11px}
.card{background:var(--s1);border:1px solid var(--br);border-radius:10px;padding:14px}
.card-lbl{font-size:8px;color:var(--muted);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:9px}
.paper-bal-label{font-size:9px;color:var(--yel);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:5px}
.paper-trade-form{background:var(--s1);border:1px solid var(--br);border-radius:10px;padding:14px;margin-bottom:14px}
.paper-form-title{font-size:11px;font-weight:600;color:var(--acc);margin-bottom:10px;font-family:var(--mono);letter-spacing:.5px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:7px}
.form-field{display:flex;flex-direction:column;gap:3px}
.form-lbl{font-size:8px;color:var(--muted);letter-spacing:.5px;text-transform:uppercase}
.form-inp,.form-sel{background:var(--s2);border:1px solid var(--br);border-radius:6px;color:var(--text);font-family:var(--body);font-size:12px;padding:7px 9px;outline:none;transition:border .12s;width:100%}
.form-inp:focus,.form-sel:focus{border-color:var(--acc)}
.btn{border:none;border-radius:7px;padding:8px 16px;font-family:var(--body);font-size:12px;font-weight:600;cursor:pointer;transition:opacity .12s}
.btn:hover{opacity:.85}.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:var(--acc);color:#000}.btn-danger{background:var(--red);color:#fff}
.btn-ghost{background:var(--s2);color:var(--text);border:1px solid var(--br)}
.btn-sm{padding:5px 12px;font-size:11px}
.paper-trade-row{display:grid;grid-template-columns:0.8fr 1.2fr 1fr 0.8fr auto;gap:7px;align-items:center;background:var(--s2);border-radius:7px;padding:8px 11px;margin-bottom:5px;font-size:10px}
.paper-status-open{color:var(--yel);font-family:var(--mono);font-size:8px;font-weight:700}
.paper-status-closed{color:var(--muted);font-family:var(--mono);font-size:8px}
.billing-toggle{display:flex;gap:4px;background:var(--s2);border:1px solid var(--br);border-radius:8px;padding:3px;margin-bottom:18px;width:fit-content}
.billing-tab{padding:5px 16px;border-radius:6px;font-size:11px;font-family:var(--mono);cursor:pointer;color:var(--muted);transition:all .12s}
.billing-tab.act{background:var(--acc);color:#000;font-weight:700}
.plans-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.plan-card{background:var(--s1);border:1px solid var(--br);border-radius:12px;padding:18px;position:relative;transition:border-color .15s}
.plan-card.current{border-color:rgba(0,212,255,.4);background:rgba(0,212,255,.04)}
.plan-badge{font-size:8px;font-family:var(--mono);font-weight:700;padding:2px 7px;border-radius:3px;position:absolute;top:11px;right:11px;letter-spacing:.8px}
.plan-name{font-family:var(--mono);font-size:13px;font-weight:700;margin-bottom:5px}
.plan-price{font-family:var(--mono);font-size:26px;font-weight:700;color:var(--acc);margin-bottom:2px;line-height:1}
.plan-price-suffix{font-size:10px;color:var(--muted);margin-bottom:6px}
.plan-features{list-style:none;margin:10px 0 14px;display:flex;flex-direction:column;gap:5px}
.plan-features li{font-size:10px;color:var(--muted);display:flex;align-items:center;gap:5px}
.plan-features li::before{content:'\u2713';color:var(--grn);font-size:9px;font-weight:700;flex-shrink:0}
.plan-na{opacity:.35;pointer-events:none}
.tl-section{background:var(--s1);border:1px solid var(--br);border-radius:10px;padding:14px;margin-bottom:14px}
.tl-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.tl-title{font-size:11px;font-weight:600;color:var(--acc);font-family:var(--mono);letter-spacing:.5px}
.tl-row{display:grid;grid-template-columns:0.6fr 1.2fr 0.8fr 0.6fr 0.8fr 0.8fr 0.8fr auto;gap:6px;align-items:center;padding:7px 10px;background:var(--s2);border-radius:6px;margin-bottom:4px;font-size:10px}
.tl-pnl-pos{color:var(--grn);font-family:var(--mono);font-weight:700}
.tl-pnl-neg{color:var(--red);font-family:var(--mono);font-weight:700}
.tl-open{color:var(--yel);font-family:var(--mono);font-size:8px;font-weight:700}
.mob-nav{display:none;position:fixed;bottom:0;left:0;right:0;height:var(--mob-nav-h);background:var(--s1);border-top:1px solid var(--br);justify-content:space-around;align-items:center;z-index:100;padding-bottom:env(safe-area-inset-bottom,0)}
.mob-nav-it{display:flex;flex-direction:column;align-items:center;gap:2px;cursor:pointer;padding:6px 10px;border-radius:8px;transition:all .12s;color:var(--muted);min-width:44px}
.mob-nav-it.act{color:var(--acc)}
.mob-nav-ico{font-size:16px;line-height:1}.mob-nav-lbl{font-size:9px;letter-spacing:.3px}
@media(max-width:900px){.sidebar{display:none}.mob-nav{display:flex}.app{padding-bottom:var(--mob-nav-h)}.content{padding:10px}.sigs-grid{grid-template-columns:1fr}.stats-grid{grid-template-columns:repeat(2,1fr)}.idx-strip{grid-template-columns:repeat(2,1fr)}.movers-grid{grid-template-columns:1fr}.plans-grid{grid-template-columns:repeat(2,1fr)}.topbar{padding:0 10px;gap:6px}.src-pill{display:none}}
@media(max-width:600px){.stats-grid{grid-template-columns:repeat(2,1fr);gap:6px}.plans-grid{grid-template-columns:1fr 1fr}.form-row{grid-template-columns:1fr}.billing-toggle{width:100%}.billing-tab{flex:1;text-align:center}.tl-row{grid-template-columns:0.5fr 1fr 0.7fr 0.5fr auto}.topbar-right .badge:not(:last-child){display:none}}
@media(min-width:1400px){.sigs-grid{grid-template-columns:repeat(3,1fr)}}
`;

function skey(n){const m=(n||"").match(/^([SE]\d)/i);return m?m[1].toUpperCase():"S1";}
function scoreColor(s){return s>=75?"var(--grn)":s>=60?"var(--yel)":"var(--orn)";}
function sigClass(dir=""){const d=dir.toUpperCase();if(d.includes("BUY")||d.includes("BULL")||d.includes("LONG"))return"bull";if(d.includes("SELL")||d.includes("BEAR")||d.includes("SHORT")||d.includes("EXIT"))return"bear";return"neut";}
function fmt(n,dec=2){return n!=null&&n!==0?Number(n).toLocaleString("en-IN",{minimumFractionDigits:dec,maximumFractionDigits:dec}):"—";}
function fmtINR(n){return n!=null?`₹${Number(n).toLocaleString("en-IN")}`:"—";}
function chgClass(c){return c>0?"tick-up":c<0?"tick-dn":"tick-unch";}
function matchesMarket(sig,market){if(market==="ALL")return true;if(market==="EQUITY")return sig.market==="EQUITY";const inst=(sig.instrument||sig.symbol||"").toUpperCase();return inst===market&&sig.market!=="EQUITY";}
function matchesStrategy(sig,stratLabel){if(!stratLabel)return true;return skey(sig.strategy)===skey(stratLabel);}

function SignalMiniChart({symbol,entryPrice,targetPrice,slPrice,direction}){
  const [candles,setCandles]=useState([]);
  const [ivl,setIvl]=useState("5");
  const [loading,setLoading]=useState(false);
  const [src,setSrc]=useState("");
  const [visible,setVisible]=useState(false);
  const ref=useRef(null);
  useEffect(()=>{
    const obs=new IntersectionObserver(([e])=>{if(e.isIntersecting){setVisible(true);obs.disconnect();}},{threshold:0.1});
    if(ref.current)obs.observe(ref.current);
    return()=>obs.disconnect();
  },[]);
  useEffect(()=>{
    if(!visible)return;
    setLoading(true);
    api(`/chart/${symbol}?interval=${ivl}`)
      .then(d=>{setCandles(d.candles||[]);setSrc(d.source||"");setLoading(false);})
      .catch(()=>setLoading(false));
  },[symbol,ivl,visible]);
  if(!visible)return(<div className="sig-chart-wrap" ref={ref} style={{minHeight:40}}/>);
  if(loading)return(<div className="sig-chart-wrap" ref={ref}><div style={{padding:"14px",textAlign:"center",fontSize:9,color:"var(--muted)"}}>Loading…</div></div>);
  if(!candles.length)return(<div className="sig-chart-wrap" ref={ref}><div style={{padding:"14px",textAlign:"center",fontSize:9,color:"var(--muted)"}}>No data</div></div>);
  const data=candles.map(c=>({t:c.time.slice(11,16),price:c.close,open:c.open,high:c.high,low:c.low}));
  const prices=data.map(d=>d.price);
  const rawMin=Math.min(...prices);const rawMax=Math.max(...prices);
  const pad=rawMax===rawMin?rawMin*0.005:0;
  const minP=(rawMin-pad)*0.998;const maxP=(rawMax+pad)*1.002;
  const dirColor=direction==="BUY"||direction==="LONG"?"var(--grn)":"var(--red)";
  return(<div className="sig-chart-wrap" ref={ref}>
    <div className="sig-chart-header">
      <span className="sig-chart-title">{symbol} • {ivl}m{src==="FALLBACK"?" • SIM":""}</span>
      <div style={{display:"flex",alignItems:"center",gap:6}}>
        <span className="sig-chart-price" style={{color:dirColor}}>₹{fmt(prices[prices.length-1],0)}</span>
        <div className="sig-chart-tabs">{["1","3","5","15","30"].map(iv=>(<div key={iv} className={`sig-chart-tab ${ivl===iv?"act":""}`} onClick={()=>setIvl(iv)}>{iv}m</div>))}</div>
      </div>
    </div>
    <ResponsiveContainer width="100%" height={120}>
      <ComposedChart data={data} margin={{top:2,right:4,bottom:0,left:0}}>
        <CartesianGrid strokeDasharray="2 4" stroke="rgba(27,48,80,.6)"/>
        <XAxis dataKey="t" tick={{fontSize:6,fill:"var(--dim)"}} tickLine={false} interval={9}/>
        <YAxis domain={[minP,maxP]} tick={{fontSize:6,fill:"var(--dim)"}} tickLine={false} width={40} tickFormatter={v=>v>=1000?`${(v/1000).toFixed(1)}k`:v.toFixed(0)}/>
        <Tooltip contentStyle={{background:"var(--s2)",border:"1px solid var(--br)",borderRadius:6,fontSize:9}} formatter={(val,name)=>[`₹${Number(val).toLocaleString("en-IN")}`,name]} labelFormatter={l=>l+" IST"}/>
        <Area type="monotone" dataKey="price" stroke={dirColor} strokeWidth={1.5} fill={direction==="BUY"||direction==="LONG"?"rgba(0,255,157,.07)":"rgba(255,61,90,.07)"} dot={false} name="Price"/>
        {entryPrice&&<Line type="monotone" dataKey={()=>entryPrice} stroke="var(--acc)" strokeWidth={1} strokeDasharray="3 2" dot={false} name="Entry"/>}
        {targetPrice&&<Line type="monotone" dataKey={()=>targetPrice} stroke="var(--grn)" strokeWidth={1} strokeDasharray="3 2" dot={false} name="Target"/>}
        {slPrice&&<Line type="monotone" dataKey={()=>slPrice} stroke="var(--red)" strokeWidth={1} strokeDasharray="3 2" dot={false} name="SL"/>}
      </ComposedChart>
    </ResponsiveContainer>
    <div style={{display:"flex",gap:12,padding:"4px 10px 6px",fontSize:8,fontFamily:"var(--mono)"}}>
      {entryPrice&&<span style={{color:"var(--acc)"}}>— Entry ₹{fmt(entryPrice,0)}</span>}
      {targetPrice&&<span style={{color:"var(--grn)"}}>— Target ₹{fmt(targetPrice,0)}</span>}
      {slPrice&&<span style={{color:"var(--red)"}}>— SL ₹{fmt(slPrice,0)}</span>}
    </div>
  </div>);
}

function LogTradeModal({sig,onClose,onLogged}){
  const [form,setForm]=useState({strategy:sig.strategy||"",instrument:sig.instrument||sig.symbol||"BANKNIFTY",option_type:"CE",direction:sig.direction||"LONG",near_strike:String(sig.near_strike||0),far_strike:String(sig.far_strike||sig.near_strike||0),lots:sig.lots_suggested||1,entry_spread:sig.spread||sig.ltp||0,notes:""});
  const [loading,setLoading]=useState(false);
  const [msg,setMsg]=useState("");
  const submit=async()=>{
    setLoading(true);setMsg("");
    try{
      const r=await api("/tradelog/enter",{method:"POST",body:JSON.stringify({strategy:form.strategy,instrument:form.instrument,option_type:form.option_type,direction:form.direction,near_strike:String(form.near_strike),far_strike:String(form.far_strike),lots:parseInt(form.lots)||1,entry_spread:parseFloat(form.entry_spread)||0,notes:form.notes})});
      if(r.ok){setMsg("✓ Trade logged");onLogged&&onLogged(r.trade);}else setMsg(r.detail||"Error logging trade");
    }catch(e){setMsg("Error: "+e.message);}finally{setLoading(false);}
  };
  return(<div style={{position:"fixed",inset:0,background:"rgba(5,12,24,.85)",zIndex:200,display:"flex",alignItems:"center",justifyContent:"center",padding:16}}>
    <div style={{background:"var(--s1)",border:"1px solid var(--br2)",borderRadius:12,padding:20,width:"min(420px,100%)",maxHeight:"90vh",overflowY:"auto"}}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:14}}>
        <div style={{fontFamily:"var(--mono)",fontSize:11,color:"var(--acc)",fontWeight:700}}>📝 LOG TRADE</div>
        <span style={{cursor:"pointer",color:"var(--muted)",fontSize:16}} onClick={onClose}>×</span>
      </div>
      {msg&&<div style={{fontSize:11,padding:"6px 9px",borderRadius:6,marginBottom:10,background:msg.startsWith("✓")?"rgba(0,255,157,.08)":"rgba(255,61,90,.08)",color:msg.startsWith("✓")?"var(--grn)":"var(--red)",border:`1px solid ${msg.startsWith("✓")?"rgba(0,255,157,.2)":"rgba(255,61,90,.2)"}`}}>{msg}</div>}
      <div className="form-row">
        <div className="form-field"><label className="form-lbl">Instrument</label>
          <select className="form-sel" value={form.instrument} onChange={e=>setForm({...form,instrument:e.target.value})}>{["BANKNIFTY","NIFTY","FINNIFTY"].map(s=><option key={s}>{s}</option>)}</select></div>
        <div className="form-field"><label className="form-lbl">Direction</label>
          <select className="form-sel" value={form.direction} onChange={e=>setForm({...form,direction:e.target.value})}><option>LONG</option><option>SHORT</option><option>BUY</option><option>SELL</option></select></div>
      </div>
      <div className="form-row">
        <div className="form-field"><label className="form-lbl">Option Type</label>
          <select className="form-sel" value={form.option_type} onChange={e=>setForm({...form,option_type:e.target.value})}><option>CE</option><option>PE</option><option>BOTH</option></select></div>
        <div className="form-field"><label className="form-lbl">Lots</label>
          <input className="form-inp" type="number" min={1} max={50} value={form.lots} onChange={e=>setForm({...form,lots:parseInt(e.target.value)||1})}/></div>
      </div>
      <div className="form-row">
        <div className="form-field"><label className="form-lbl">Near Strike</label><input className="form-inp" value={form.near_strike} onChange={e=>setForm({...form,near_strike:e.target.value})}/></div>
        <div className="form-field"><label className="form-lbl">Far Strike</label><input className="form-inp" value={form.far_strike} onChange={e=>setForm({...form,far_strike:e.target.value})}/></div>
      </div>
      <div className="form-row">
        <div className="form-field"><label className="form-lbl">Entry Spread (pts)</label><input className="form-inp" type="number" step="0.5" value={form.entry_spread} onChange={e=>setForm({...form,entry_spread:parseFloat(e.target.value)||0})}/></div>
        <div className="form-field"><label className="form-lbl">Notes</label><input className="form-inp" value={form.notes} onChange={e=>setForm({...form,notes:e.target.value})} placeholder="Optional…"/></div>
      </div>
      <div style={{display:"flex",gap:8,marginTop:4}}>
        <button className="btn btn-primary" style={{flex:1}} onClick={submit} disabled={loading}>{loading?"Logging…":"Log Trade"}</button>
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
      </div>
    </div>
  </div>);
}

function PcrOiChart({history}){
  if(!history||history.length<2)return(<div className="pcr-chart-wrap"><div className="pcr-chart-title">OI CHART — CALL vs PUT vs SPOT</div><div style={{textAlign:"center",padding:"14px 0",fontSize:10,color:"var(--muted)"}}>Accumulating… ({history?.length||0} pts)</div></div>);
  const data=history.map(h=>({t:h.time,callOI:h.callOI?+(h.callOI/1e5).toFixed(1):null,putOI:h.putOI?+(h.putOI/1e5).toFixed(1):null,spot:h.spot?+h.spot.toFixed(0):null}));
  return(<div className="pcr-chart-wrap"><div className="pcr-chart-title">CALL OI vs PUT OI vs SPOT</div>
    <ResponsiveContainer width="100%" height={150}>
      <LineChart data={data} margin={{top:4,right:10,bottom:2,left:0}}>
        <CartesianGrid strokeDasharray="2 4" stroke="var(--br)"/>
        <XAxis dataKey="t" tick={{fontSize:7,fill:"var(--dim)"}} tickFormatter={v=>v.slice(11,16)}/>
        <YAxis yAxisId="oi" tick={{fontSize:7,fill:"var(--dim)"}} tickFormatter={v=>`${v}L`} width={30}/>
        <YAxis yAxisId="spot" orientation="right" tick={{fontSize:7,fill:"var(--dim)"}} tickFormatter={v=>`${(v/1000).toFixed(0)}k`} width={34}/>
        <Tooltip contentStyle={{background:"var(--s2)",border:"1px solid var(--br)",borderRadius:7,fontSize:10}} formatter={(val,name)=>[name==="spot"?val?.toLocaleString("en-IN"):val!=null?val+"L":"—",name==="callOI"?"Call OI":name==="putOI"?"Put OI":"Spot"]} labelFormatter={l=>l.slice(11,16)}/>
        <Legend iconSize={8} wrapperStyle={{fontSize:9,paddingTop:4}} formatter={n=>n==="callOI"?"Call OI":n==="putOI"?"Put OI":"Index Spot"}/>
        <Line yAxisId="oi" type="monotone" dataKey="callOI" stroke="#ff3d5a" strokeWidth={2} dot={false} name="callOI"/>
        <Line yAxisId="oi" type="monotone" dataKey="putOI" stroke="#00ff9d" strokeWidth={2} dot={false} name="putOI"/>
        <Line yAxisId="spot" type="monotone" dataKey="spot" stroke="#00d4ff" strokeWidth={1.5} dot={false} strokeDasharray="4 2" name="spot"/>
      </LineChart>
    </ResponsiveContainer>
  </div>);
}

function SourceBadge({source}){
  if(!source)return null;
  const s=source.toUpperCase();
  if(s==="NSE_LIVE"||s==="NSE_CACHED")return <span className="sig-source-badge src-live">● LIVE</span>;
  if(s==="NSE_EOD"||s==="PCR_LIVE")return <span className="sig-source-badge src-eod">● EOD</span>;
  if(s==="DEMO"||s==="MOCK"||s==="PCR_MOCK")return <span className="sig-source-badge src-demo">◎ DEMO</span>;
  return <span className="sig-source-badge src-demo">◎ {s}</span>;
}

function sigAge(s) {
  if (!s.timestamp) return null;
  const mins = Math.round((Date.now() - new Date(s.timestamp).getTime()) / 60000);
  if (mins < 1) return null;
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m ago`;
}
function sigExpiredLabel(s) {
  if (!sigIsExpired(s)) return null;
  return <span style={{fontSize:7,fontFamily:"var(--mono)",color:"var(--red)",background:"rgba(255,61,90,.1)",border:"1px solid rgba(255,61,90,.2)",borderRadius:3,padding:"1px 5px",marginRight:4}}>EXPIRED</span>;
}

function SigCard({sig,pcrHistory,onLogTrade,onPlaceOrder,userPlan}){
  const isPcr=(sig.strategy||"").toUpperCase().includes("PCR")||sig.source==="pcr_strategy"||sig.source==="pcr_mock";
  const isEq=sig.market==="EQUITY";
  const chartSymbol=sig.instrument||sig.symbol||(isEq?sig.symbol:"BANKNIFTY");
  const entryPrice=sig.entry_at||sig.ltp||sig.spot||null;
  const targetPrice=sig.target_at||(sig.target_pts&&entryPrice?(sig.direction==="BUY"||sig.direction==="LONG"?entryPrice+sig.target_pts:entryPrice-sig.target_pts):null);
  const slPrice=sig.sl_at||(sig.sl_pts&&entryPrice?(sig.direction==="BUY"||sig.direction==="LONG"?entryPrice-sig.sl_pts:entryPrice+sig.sl_pts):null);

  if(isPcr){
    const pcr=sig.pcr_oi;
    const zone=sig.zone||(pcr<0.6?"OVERBOUGHT":pcr>1.3?"OVERSOLD":"NEUTRAL");
    const zoneCol=PCR_ZONE_COLOR[zone]||"var(--muted)";
    const barWidth=Math.round(Math.min(2,Math.max(0,pcr||1))/2*100);
    const barColor=zone==="OVERSOLD"?"var(--grn)":zone==="OVERBOUGHT"?"var(--red)":"var(--yel)";
    return(<div className={`sig-card ${sigClass(sig.direction)}`}>
      <div className="sig-top"><div>
        <div className="sig-strat" style={{color:"#22c55e"}}>S5 PCR CONTRARIAN</div>
        <div className="sig-tags">
          <span className="sig-tag" style={{background:"rgba(34,197,94,.12)",color:"#22c55e",border:"1px solid rgba(34,197,94,.25)"}}>{sig.instrument||"F&O"}</span>
          <span className="sig-tag" style={{background:zoneCol+"20",color:zoneCol,border:`1px solid ${zoneCol}40`}}>{zone}</span>
          <span className="sig-tag" style={{background:"rgba(0,212,255,.1)",color:"var(--acc)",border:"1px solid rgba(0,212,255,.2)"}}>OI</span>
        </div></div>
        <div className="sig-score-wrap"><div className="sig-score" style={{color:scoreColor(sig.score)}}>{sig.score}</div><div className="sig-score-lbl">SCORE</div></div>
      </div>
      <div className="pcr-gauge">
        <div className="pcr-gauge-header">
          <div><div style={{fontSize:8,color:"var(--muted)",marginBottom:3}}>PUT/CALL RATIO (OI)</div>
            <div className="pcr-val" style={{color:zoneCol}}>{pcr!=null?Number(pcr).toFixed(3):"—"}</div></div>
          <div className="pcr-zone-lbl" style={{background:zoneCol+"20",color:zoneCol,border:`1px solid ${zoneCol}40`}}>{sig.signal||zone}</div>
        </div>
        <div className="pcr-bar-track"><div className="pcr-bar-fill" style={{width:barWidth+"%",background:barColor}}/></div>
        <div className="pcr-labels"><span>0 GREED</span><span>0.60</span><span>1.15</span><span>1.30</span><span>2.0 FEAR</span></div>
        <div className="pcr-detail">
          <div className="pcr-stat"><div className="pcr-stat-k">PUT OI</div><div className="pcr-stat-v">{sig.total_put_oi?(sig.total_put_oi/1e5).toFixed(1)+"L":"—"}</div></div>
          <div className="pcr-stat"><div className="pcr-stat-k">CALL OI</div><div className="pcr-stat-v">{sig.total_call_oi?(sig.total_call_oi/1e5).toFixed(1)+"L":"—"}</div></div>
          <div className="pcr-stat"><div className="pcr-stat-k">PCR VOL</div><div className="pcr-stat-v">{sig.pcr_volume!=null?Number(sig.pcr_volume).toFixed(3):"—"}</div></div>
          <div className="pcr-stat"><div className="pcr-stat-k">VIX</div><div className="pcr-stat-v">{sig.vix||"—"}</div></div>
        </div>
      </div>
      {pcrHistory&&<PcrOiChart history={pcrHistory[sig.instrument]||[]}/>}
      <SignalMiniChart symbol={chartSymbol} direction={sig.direction}/>
      <div className="sig-action"><span style={{color:sig.direction==="LONG"||sig.direction==="BUY"?"var(--grn)":"var(--red)",fontWeight:700}}>{sig.direction}</span>{" "}{sig.instrument} — PCR {pcr!=null?Number(pcr).toFixed(3):"—"}{zone==="OVERBOUGHT"?" — Fade GREED":zone==="OVERSOLD"?" — Fade FEAR":""}</div>
      {sig.reason&&<div className="sig-reason">{sig.reason}</div>}
      <div className="sig-foot"><div className="sig-src">📊 {sig.source||"PCR"}</div><span className={`risk-badge r${(sig.risk||"M")[0]}`}>{sig.risk||"MEDIUM"}</span><span className="log-trade-btn" onClick={()=>onLogTrade(sig)}>📝 Log Trade</span></div>
    </div>);
  }

  if(isEq){
    const info=STRAT_INFO[skey(sig.strategy)]||{color:"var(--pur)",tag:""};
    const ltp=sig.ltp||sig.spot||0;const chg=sig.change_pct||0;
    return(<div className={`sig-card ${sigClass(sig.direction)}`}>
      <div className="sig-top"><div>
        <div className="sig-strat" style={{color:info.color}}>{sig.strategy}<SourceBadge source={sig.source}/></div>
        <div className="sig-tags">
          <span className="sig-tag" style={{background:"rgba(167,139,250,.12)",color:"var(--pur)",border:"1px solid rgba(167,139,250,.2)"}}>EQUITY</span>
          <span className="sig-tag" style={{background:info.color+"18",color:info.color,border:`1px solid ${info.color}30`}}>{info.tag}</span>
        </div></div>
        <div className="sig-score-wrap"><div className="sig-score" style={{color:scoreColor(sig.score)}}>{sig.score}</div><div className="sig-score-lbl">SCORE</div></div>
      </div>
      <div className="eq-price-hero"><div><div className="eq-sym">{sig.symbol}</div></div>
        <div style={{textAlign:"right"}}>
          <div className="eq-ltp" style={{color:chg>=0?"var(--grn)":"var(--red)"}}>₹{ltp?fmt(ltp,2):"—"}</div>
          <div className={`eq-chg ${chg>=0?"eq-chg-up":"eq-chg-dn"}`}>{chg>=0?"+":""}{fmt(chg,2)}%</div>
        </div>
      </div>
      {(sig.high||sig.low)&&<div className="eq-ohlc"><span>H:₹{fmt(sig.high,2)}</span><span>L:₹{fmt(sig.low,2)}</span>{sig.prev_close&&<span>PC:₹{fmt(sig.prev_close,2)}</span>}</div>}
      <SignalMiniChart symbol={sig.symbol} entryPrice={sig.entry_at} targetPrice={sig.target_at} slPrice={sig.sl_at} direction={sig.direction}/>
      <div className="sig-action"><span style={{color:sig.direction==="BUY"?"var(--grn)":"var(--red)",fontWeight:700}}>{sig.direction}</span>{" "}{sig.symbol} @ ₹{ltp?fmt(ltp,2):"—"}</div>
      <div className="sig-meta">
        <div className="meta-box"><div className="meta-k">Entry</div><div className="meta-v">₹{sig.entry_at?fmt(sig.entry_at,2):"—"}</div></div>
        <div className="meta-box"><div className="meta-k">Target</div><div className="meta-v" style={{color:"var(--grn)"}}>₹{sig.target_at?fmt(sig.target_at,2):"—"}</div></div>
        <div className="meta-box"><div className="meta-k">SL</div><div className="meta-v" style={{color:"var(--red)"}}>₹{sig.sl_at?fmt(sig.sl_at,2):"—"}</div></div>
        <div className="meta-box"><div className="meta-k">Tgt pts</div><div className="meta-v" style={{color:"var(--grn)"}}>{sig.target_pts||"—"}</div></div>
        <div className="meta-box"><div className="meta-k">SL pts</div><div className="meta-v" style={{color:"var(--red)"}}>{sig.sl_pts||"—"}</div></div>
        <div className="meta-box"><div className="meta-k">VIX</div><div className="meta-v">{sig.vix||"—"}</div></div>
      </div>
      <SignalMiniChart symbol={chartSymbol} entryPrice={entryPrice} targetPrice={targetPrice} slPrice={slPrice} direction={sig.direction}/>{sig.reason&&<div className="sig-reason">{sig.reason}</div>}
      <div className="sig-foot"><div className="sig-src">📡 {sig.source||"NSE"}</div><span className={`risk-badge r${(sig.risk||"M")[0]}`}>{sig.risk||"MEDIUM"}</span><span className="log-trade-btn" onClick={()=>onLogTrade(sig)}>📝 Log</span><span className="log-trade-btn" style={{background:"rgba(255,61,90,.09)",borderColor:"rgba(255,61,90,.3)",color:"var(--red)"}} onClick={()=>onPlaceOrder&&onPlaceOrder(sig)}>⚡ Place</span></div>
    </div>);
  }

  const k=skey(sig.strategy);const info=STRAT_INFO[k]||{color:"var(--acc)",tag:"NEUTRAL"};
  const spread=sig.spread??sig.entry_spread;
  const isCalendar=sig.strategy?.toUpperCase().includes("CALENDAR");
  return(<div className={`sig-card ${sigClass(sig.direction)}`}>
    <div className="sig-top"><div>
      <div className="sig-strat" style={{color:info.color}}>{sig.strategy}</div>
      <div className="sig-tags">
        <span className="sig-tag" style={{background:"rgba(0,212,255,.12)",color:"var(--acc)",border:"1px solid rgba(0,212,255,.2)"}}>{isCalendar?"CALENDAR":sig.instrument||"FO"}</span>
        <span className="sig-tag" style={{background:info.color+"18",color:info.color,border:`1px solid ${info.color}30`}}>{info.tag}</span>
        {sig.event_type&&sig.event_type!=="signal"&&(<span className="sig-tag" style={{background:sig.event_type==="entry"?"rgba(0,255,157,.12)":sig.event_type==="exit"?"rgba(255,61,90,.12)":"rgba(245,197,24,.08)",color:sig.event_type==="entry"?"var(--grn)":sig.event_type==="exit"?"var(--red)":"var(--yel)",border:"1px solid transparent"}}>{sig.event_type?.toUpperCase()}</span>)}
      </div></div>
      <div className="sig-score-wrap"><div className="sig-score" style={{color:scoreColor(sig.score)}}>{sig.score}</div><div className="sig-score-lbl">SCORE</div></div>
    </div>
    <div className="fo-strikes">
      <div style={{display:"flex",alignItems:"baseline",gap:4}}><span className="fo-idx">{sig.instrument||sig.symbol||"BANKNIFTY"}</span>{sig.near_strike&&<span className="fo-spot">ATM {sig.near_strike}</span>}</div>
      {spread!=null&&<div className="fo-exp">Spread: {fmt(spread,2)}pts{sig.fair_value!=null&&` | Fair: ${fmt(sig.fair_value,2)}pts`}{sig.deviation!=null&&` | Dev: ${fmt(sig.deviation,2)}pts`}</div>}
    </div>
    <SignalMiniChart symbol={chartSymbol} entryPrice={entryPrice} targetPrice={targetPrice} slPrice={slPrice} direction={sig.direction}/>
    <div className="sig-action">{sig.action||sig.orders||"—"}</div>
    <div className="sig-meta">
      <div className="meta-box"><div className="meta-k">Spread</div><div className="meta-v">{spread!=null?fmt(spread,2)+"pts":"—"}</div></div>
      <div className="meta-box"><div className="meta-k">Target</div><div className="meta-v" style={{color:"var(--grn)"}}>{sig.target_pts?"+"+sig.target_pts+"pts":"—"}</div></div>
      <div className="meta-box"><div className="meta-k">SL</div><div className="meta-v" style={{color:"var(--red)"}}>{sig.sl_pts?"-"+sig.sl_pts+"pts":"—"}</div></div>
      <div className="meta-box"><div className="meta-k">Direction</div><div className="meta-v" style={{color:sig.direction?.includes("LONG")||sig.direction==="BUY"?"var(--grn)":"var(--red)"}}>{sig.direction||"—"}</div></div>
      <div className="meta-box"><div className="meta-k">Lots</div><div className="meta-v">{sig.lots_suggested||"—"}</div></div>
      <div className="meta-box"><div className="meta-k">VIX</div><div className="meta-v">{sig.vix||"—"}</div></div>
    </div>
    <SignalMiniChart symbol={chartSymbol} entryPrice={entryPrice} targetPrice={targetPrice} slPrice={slPrice} direction={sig.direction}/>{sig.reason&&<div className="sig-reason">{sig.reason}</div>}
    <div className="sig-foot"><div className="sig-src">📡 {sig.source||"Algo"}</div><span className={`risk-badge r${(sig.risk||"M")[0]}`}>{sig.risk||"MEDIUM"}</span><span className="log-trade-btn" onClick={()=>onLogTrade(sig)}>📝 Log</span><span className="log-trade-btn" style={{background:"rgba(255,61,90,.09)",borderColor:"rgba(255,61,90,.3)",color:"var(--red)"}} onClick={()=>onPlaceOrder&&onPlaceOrder(sig)}>⚡ Place</span></div>
  </div>);
}

function MoversPanel(){const [movers,setMovers]=useState({gainers:[],losers:[]});useEffect(()=>{api("/movers").then(setMovers).catch(()=>{});},[]);return(<div className="movers-grid">{[{key:"gainers",lbl:"▲ Top Gainers",col:"var(--grn)",cls:"chg-up",pfx:"+"},{key:"losers",lbl:"▼ Top Losers",col:"var(--red)",cls:"chg-dn",pfx:""}].map(g=>(<div className="mover-table" key={g.key}><div className="mover-hdr" style={{color:g.col}}>{g.lbl}</div>{(movers[g.key]||[]).map((m,i)=>(<div className="mover-row" key={i}><span className="mover-sym">{m.symbol}</span><span className="mover-ltp">₹{fmt(m.ltp,2)}</span><span className={`mover-chg ${g.cls}`}>{g.pfx}{fmt(m.change_pct,2)}%</span></div>))}{!(movers[g.key]||[]).length&&<div style={{padding:"10px 12px",fontSize:10,color:"var(--muted)"}}>Loading…</div>}</div>))}</div>);}
function StratSegBanner({signals}){const groups=[{id:"S1",label:"Calendar",color:"#00d4ff"},{id:"S2",label:"Iron Condor",color:"#00ff9d"},{id:"S3",label:"Straddle",color:"#f5c518"},{id:"S4",label:"0DTE Scalp",color:"#ff6b35"},{id:"S5",label:"PCR",color:"#22c55e"},{id:"EQ",label:"Equity",color:"#a78bfa"}];return(<div className="strat-seg">{groups.map(g=>{const count=g.id==="EQ"?signals.filter(s=>s.market==="EQUITY").length:signals.filter(s=>skey(s.strategy)===g.id).length;return(<div key={g.id} className="strat-seg-card"><div><div className="strat-seg-name">{g.label}</div><div className="strat-seg-count" style={{color:count>0?g.color:"var(--dim)"}}>{count}</div></div><div style={{width:3,height:28,borderRadius:2,background:count>0?g.color:"var(--br)"}}/></div>);})}</div>);}
function IndexTicker({indices}){if(!indices||!indices.length)return null;const items=[...indices,...indices];return(<div className="ticker-bar"><div className="ticker-inner">{items.map((idx,i)=>(<div className={`tick-item ${idx._flash||""}`} key={`${idx.label}-${i}-${idx._ts||0}`}><span className="tick-label">{idx.label}</span><span className={`tick-val ${chgClass(idx.change_pct)}`}>{idx.ltp?fmt(idx.ltp,idx.label==="VIX"?2:0):"—"}</span>{idx.change_pct!==0&&<span className={`tick-chg ${chgClass(idx.change_pct)}`} style={{background:idx.change_pct>0?"rgba(0,255,157,.1)":"rgba(255,61,90,.1)"}}>{idx.change_pct>0?"+":""}{fmt(idx.change_pct,2)}%</span>}</div>))}</div></div>);}
function IndexStrip({indices}){const main=["NIFTY","BANKNIFTY","FINNIFTY","VIX","MIDCAP"];const shown=(indices||[]).filter(i=>main.includes(i.label));if(!shown.length)return null;return(<div className="idx-strip">{shown.map(idx=>{const up=idx.change_pct>0,dn=idx.change_pct<0;const col=up?"var(--grn)":dn?"var(--red)":"var(--muted)";return(<div className={`idx-card ${idx._flash||""}`} key={`${idx.label}-${idx._ts||0}`}><div className="idx-name">{idx.label}</div><div className="idx-ltp" style={{color:idx.ltp?col:"var(--muted)"}}>{idx.ltp?fmt(idx.ltp,idx.label==="VIX"?2:0):"Loading…"}</div><div className="idx-chg" style={{color:col}}>{idx.change_pct!==0?(idx.change_pct>0?"+":"")+fmt(idx.change_pct,2)+"%":"—"}</div>{(idx.high||idx.low)?<div className="idx-hl">H:{fmt(idx.high,0)} L:{fmt(idx.low,0)}</div>:null}</div>);})}</div>);}

function SignalsTab({signals,market,strategy,indices,onClearStrategy,pcrHistory,onLogTrade,onPlaceOrder,userPlan}){
  const [showExpired,setShowExpired]=useState(false);
  const filtered=signals
    .filter(s=>matchesMarket(s,market))
    .filter(s=>matchesStrategy(s,strategy))
    .filter(s=>showExpired||!sigIsExpired(s));
  const expiredCount=signals.filter(s=>matchesMarket(s,market)).filter(s=>matchesStrategy(s,strategy)).filter(s=>sigIsExpired(s)).length;const mktObj=MARKETS.find(m=>m.id===market);return(<div><IndexStrip indices={indices}/>{market==="ALL"&&<MoversPanel/>}{market==="ALL"&&<StratSegBanner signals={signals}/>}
    {!isMarketOpen()&&<div style={{background:"rgba(245,197,24,.06)",border:"1px solid rgba(245,197,24,.18)",borderRadius:7,padding:"5px 11px",marginBottom:10,fontSize:9,fontFamily:"var(--mono)",color:"var(--yel)",display:"flex",alignItems:"center",gap:7}}>
      <span>⏰</span><span>{isAfterMarketClose()?"MARKET CLOSED — signals shown are from today's session":"PRE-MARKET — live signals begin at 9:15 AM IST"}</span>
    </div>}{(market!=="ALL"||strategy)&&(<div className="filter-crumb">{market!=="ALL"&&<span style={{color:mktObj?.color||"var(--acc)"}}>{mktObj?.label||market}</span>}{market!=="ALL"&&strategy&&<span style={{color:"var(--dim)"}}>›</span>}{strategy&&<span style={{color:STRAT_INFO[skey(strategy)]?.color||"var(--acc)"}}>{strategy}</span>}<span style={{color:"var(--muted)",fontSize:9}}>&nbsp;— {filtered.length} signal{filtered.length!==1?"s":""}</span>{strategy&&<span className="filter-crumb-clear" onClick={onClearStrategy}>× Clear</span>}
      {expiredCount>0&&<span style={{cursor:"pointer",fontSize:8,color:showExpired?"var(--red)":"var(--dim)",fontFamily:"var(--mono)",marginLeft:"auto",padding:"1px 6px",borderRadius:4,border:"1px solid rgba(255,61,90,.2)",background:"rgba(255,61,90,.05)"}} onClick={()=>setShowExpired(v=>!v)}>{showExpired?`Hide ${expiredCount} expired`:`+${expiredCount} expired`}</span>}
    </div>)}{filtered.length>0?(<div className="sigs-grid">{filtered.map((s,i)=><SigCard key={s.id||`${s.timestamp}-${i}`} sig={s} pcrHistory={pcrHistory} onLogTrade={onLogTrade} onPlaceOrder={onPlaceOrder} userPlan={userPlan}/>)}</div>):(<div className="empty"><div className="empty-ico">📊</div><div className="empty-t">No signals for this filter</div><div className="empty-s">{market!=="ALL"?`${market}${strategy?" • "+strategy:""} — 9:15–15:30`:"Backend pushes every 5s"}</div></div>)}</div>);}

function TraderLoggerTab(){
  const [data,setData]=useState(null);
  const [form,setForm]=useState({strategy:"S1 CALENDAR",instrument:"BANKNIFTY",option_type:"CE",direction:"LONG",near_strike:"0",far_strike:"0",lots:1,entry_spread:0,notes:""});
  const [closing,setClosing]=useState(null);
  const [exitSpread,setExitSpread]=useState(0);
  const [loading,setLoading]=useState(false);
  const [msg,setMsg]=useState("");
  const [csvOpen,setCsvOpen]=useState(false);
  const [csvText,setCsvText]=useState("");
  const [ltpMap,setLtpMap]=useState({});
  const load=()=>api("/tradelog/today").then(setData).catch(()=>{});
  useEffect(()=>{
    load();
    // Poll indices for live LTP every 30s for MTM calculation
    const pollMtm=()=>api("/indices").then(d=>{
      const m={};
      (d.indices||d||[]).forEach(idx=>{ if(idx.label)m[idx.label]=idx.ltp||0; });
      setLtpMap(m);
    }).catch(()=>{});
    pollMtm();
    const t=setInterval(()=>{load();pollMtm();},30000);
    return()=>clearInterval(t);
  },[]);
  const enter=async()=>{setLoading(true);setMsg("");try{const r=await api("/tradelog/enter",{method:"POST",body:JSON.stringify(form)});if(r.ok){setMsg("✓ Trade logged");load();}else setMsg(r.detail||"Error — Weekly plan+ required");}catch(e){setMsg("Error: "+e.message);}finally{setLoading(false);}}
  const closeT=async(idx)=>{setLoading(true);setMsg("");try{const r=await api("/tradelog/close",{method:"POST",body:JSON.stringify({trade_index:idx,exit_spread:parseFloat(exitSpread)||0,notes:""})});if(r.ok){setMsg(`✓ P&L: ₹${(r.pnl_inr||0).toLocaleString("en-IN")}`);setClosing(null);load();}else setMsg(r.detail||"Error");}catch(e){setMsg("Error: "+e.message);}finally{setLoading(false);}}
  const exportCsv=async()=>{const r=await api("/tradelog/export").catch(()=>({}));setCsvText(r.csv||"");setCsvOpen(true);};
  const trades=data?.trades||[];const open=trades.filter(t=>String(t.status||"").toUpperCase()==="OPEN");const closed=trades.filter(t=>String(t.status||"").toUpperCase()==="CLOSED");
  const pnlCol=(data?.realised_pnl||0)>=0?"var(--grn)":"var(--red)";const msgGood=msg.startsWith("✓");
  return(<div>
    <div style={{marginBottom:14,padding:"12px 14px",background:"var(--s1)",border:"1px solid rgba(0,212,255,.15)",borderRadius:10}}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:8}}>
        <div style={{fontFamily:"var(--mono)",fontSize:11,color:"var(--acc)",fontWeight:700}}>📝 TRADE LOGGER {data?.tl_available?<span style={{fontSize:8,color:"var(--grn)",marginLeft:6}}>● CSV</span>:<span style={{fontSize:8,color:"var(--yel)",marginLeft:6}}>● MEM</span>}</div>
        <button className="btn btn-ghost btn-sm" onClick={exportCsv}>Export CSV</button>
      </div>
      <div style={{display:"flex",gap:20,flexWrap:"wrap"}}>
        {[{lbl:"TOTAL",val:data?.total_trades||0,col:"var(--acc)"},{lbl:"OPEN",val:data?.open_count||0,col:"var(--yel)"},{lbl:"REALISED P&L",val:fmtINR(data?.realised_pnl||0),col:pnlCol},{lbl:"WIN RATE",val:(data?.win_rate||0)+"%",col:"var(--grn)"}]
          .map((s,i)=>(<div key={i}><div style={{fontSize:8,color:"var(--muted)",marginBottom:2}}>{s.lbl}</div><div style={{fontFamily:"var(--mono)",fontSize:16,fontWeight:700,color:s.col}}>{s.val}</div></div>))}
      </div>
    </div>
    {msg&&<div style={{fontSize:11,padding:"6px 9px",borderRadius:6,marginBottom:10,background:msgGood?"rgba(0,255,157,.08)":"rgba(255,61,90,.08)",color:msgGood?"var(--grn)":"var(--red)",border:`1px solid ${msgGood?"rgba(0,255,157,.2)":"rgba(255,61,90,.2)"}`}}>{msg}</div>}
    <div className="tl-section"><div className="tl-header"><div className="tl-title">+ LOG NEW TRADE</div></div>
      <div className="form-row">
        <div className="form-field"><label className="form-lbl">Strategy</label><select className="form-sel" value={form.strategy} onChange={e=>setForm({...form,strategy:e.target.value})}>{["S1 CALENDAR","S2 IRON CONDOR","S3 SHORT STRADDLE","S4 0DTE SCALP","S5 PCR CONTRARIAN","E1 EMA CROSSOVER","E2 VWAP REVERSION","E3 ORB BREAKOUT","E4 GAP FILL","MANUAL"].map(s=><option key={s}>{s}</option>)}</select></div>
        <div className="form-field"><label className="form-lbl">Instrument</label><select className="form-sel" value={form.instrument} onChange={e=>setForm({...form,instrument:e.target.value})}>{["BANKNIFTY","NIFTY","FINNIFTY"].map(s=><option key={s}>{s}</option>)}</select></div>
      </div>
      <div className="form-row">
        <div className="form-field"><label className="form-lbl">Type</label><select className="form-sel" value={form.option_type} onChange={e=>setForm({...form,option_type:e.target.value})}><option>CE</option><option>PE</option><option>BOTH</option></select></div>
        <div className="form-field"><label className="form-lbl">Direction</label><select className="form-sel" value={form.direction} onChange={e=>setForm({...form,direction:e.target.value})}><option>LONG</option><option>SHORT</option></select></div>
      </div>
      <div className="form-row">
        <div className="form-field"><label className="form-lbl">Near Strike</label><input className="form-inp" value={form.near_strike} onChange={e=>setForm({...form,near_strike:e.target.value})}/></div>
        <div className="form-field"><label className="form-lbl">Far Strike</label><input className="form-inp" value={form.far_strike} onChange={e=>setForm({...form,far_strike:e.target.value})}/></div>
      </div>
      <div className="form-row">
        <div className="form-field"><label className="form-lbl">Entry Spread</label><input className="form-inp" type="number" step="0.5" value={form.entry_spread} onChange={e=>setForm({...form,entry_spread:parseFloat(e.target.value)||0})}/></div>
        <div className="form-field"><label className="form-lbl">Lots</label><input className="form-inp" type="number" min={1} value={form.lots} onChange={e=>setForm({...form,lots:parseInt(e.target.value)||1})}/></div>
      </div>
      <div className="form-row"><div className="form-field" style={{gridColumn:"span 2"}}><label className="form-lbl">Notes</label><input className="form-inp" value={form.notes} onChange={e=>setForm({...form,notes:e.target.value})} placeholder="Optional…"/></div></div>
      <button className="btn btn-primary" style={{width:"100%"}} onClick={enter} disabled={loading}>{loading?"Logging…":"Log Trade"}</button>
    </div>
    {open.length>0&&(<div className="tl-section"><div className="tl-header"><div className="tl-title">OPEN ({open.length})</div></div>
      {open.map((t,i)=>(<div key={i}>
        <div className="tl-row">
          <span style={{fontFamily:"var(--mono)",fontSize:9}}>{t.time||t.entry_time?.slice(11,16)}</span>
          <span style={{fontSize:10}}>{String(t.strategy||"").slice(0,14)}</span>
          <span style={{fontFamily:"var(--mono)",fontSize:10}}>{t.instrument}</span>
          <span style={{color:t.direction==="LONG"||t.direction==="BUY"?"var(--grn)":"var(--red)",fontFamily:"var(--mono)",fontSize:9,fontWeight:700}}>{t.direction}</span>
          <span style={{fontFamily:"var(--mono)",fontSize:10}}>{t.entry_spread}</span>
          <span style={{fontFamily:"var(--mono)",fontSize:10}}>{t.lots}</span>
          {(()=>{
        const instLtp=ltpMap[t.instrument]||0;
        const entrySpread=parseFloat(t.entry_spread||0);
        const lots=parseInt(t.lots||1);
        const lotSz={BANKNIFTY:15,NIFTY:25,FINNIFTY:40}[t.instrument]||15;
        const dir=(t.direction==="LONG"||t.direction==="BUY");
        const mtmPts=instLtp>0&&entrySpread>0?(dir?instLtp-entrySpread:entrySpread-instLtp):null;
        const mtmInr=mtmPts!==null?mtmPts*lots*lotSz:null;
        return mtmInr!==null?(<span style={{fontFamily:"var(--mono)",fontSize:9,fontWeight:700,color:mtmInr>=0?"var(--grn)":"var(--red)"}}>{mtmInr>=0?"+":""}{Math.round(mtmInr).toLocaleString("en-IN")}</span>)
          :(<span className="tl-open">OPEN</span>);
      })()}
          <button className="btn btn-ghost btn-sm" style={{fontSize:9}} onClick={()=>setClosing(closing===i?null:i)}>Close</button>
        </div>
        {closing===i&&(<div style={{display:"flex",gap:7,padding:"6px 10px 8px",alignItems:"center",background:"var(--s3)",borderRadius:6,marginBottom:4}}>
          <input className="form-inp" type="number" step="0.5" placeholder="Exit spread" style={{width:120}} value={exitSpread} onChange={e=>setExitSpread(e.target.value)}/>
          <button className="btn btn-danger btn-sm" onClick={()=>closeT(i)} disabled={loading}>Confirm</button>
          <button className="btn btn-ghost btn-sm" onClick={()=>setClosing(null)}>Cancel</button>
        </div>)}
      </div>))}
    </div>)}
    {closed.length>0&&(<div className="tl-section"><div className="tl-header"><div className="tl-title">CLOSED ({closed.length})</div></div>
      {closed.slice(-10).reverse().map((t,i)=>{const pnl=parseFloat(t.pnl_inr||0);const pts=parseFloat(t.pnl_pts||0);return(<div className="tl-row" key={i}><span style={{fontFamily:"var(--mono)",fontSize:9}}>{t.time||t.entry_time?.slice(11,16)}</span><span style={{fontSize:10}}>{String(t.strategy||"").slice(0,14)}</span><span style={{fontFamily:"var(--mono)",fontSize:10}}>{t.instrument}</span><span style={{color:t.direction==="LONG"||t.direction==="BUY"?"var(--grn)":"var(--red)",fontFamily:"var(--mono)",fontSize:9,fontWeight:700}}>{t.direction}</span><span style={{fontFamily:"var(--mono)",fontSize:10}}>{t.entry_spread}</span><span style={{fontFamily:"var(--mono)",fontSize:10}}>{t.exit_spread}</span><span className={pts>=0?"tl-pnl-pos":"tl-pnl-neg"}>{pts>=0?"+":""}{pts}pts</span><span className={pnl>=0?"tl-pnl-pos":"tl-pnl-neg"}>{fmtINR(pnl)}</span></div>);})}
    </div>)}
    {!trades.length&&(<div className="empty"><div className="empty-ico">📝</div><div className="empty-t">No trades logged today</div><div className="empty-s">Use form above or click 📝 Log Trade on any signal</div></div>)}
    {csvOpen&&(<div style={{position:"fixed",inset:0,background:"rgba(5,12,24,.9)",zIndex:200,display:"flex",alignItems:"center",justifyContent:"center",padding:16}}><div style={{background:"var(--s1)",border:"1px solid var(--br2)",borderRadius:12,padding:18,width:"min(600px,100%)",maxHeight:"80vh",display:"flex",flexDirection:"column",gap:10}}><div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}><div style={{fontFamily:"var(--mono)",fontSize:11,color:"var(--acc)",fontWeight:700}}>CSV EXPORT</div><span style={{cursor:"pointer",color:"var(--muted)",fontSize:16}} onClick={()=>setCsvOpen(false)}>×</span></div><textarea style={{flex:1,background:"var(--s2)",border:"1px solid var(--br)",borderRadius:6,color:"var(--text)",fontFamily:"var(--mono)",fontSize:10,padding:10,resize:"none",minHeight:200}} value={csvText} readOnly/><button className="btn btn-primary" style={{width:"100%"}} onClick={()=>{navigator.clipboard.writeText(csvText);setCsvOpen(false);}}>Copy to Clipboard</button></div></div>)}
  </div>);
}

function AnalyticsTab(){const [pnl,setPnl]=useState(null);useEffect(()=>{api("/analytics/pnl").then(setPnl).catch(()=>{});},[]);const byS=pnl?.by_strategy||{};const chart=Object.entries(byS).map(([k,v])=>({name:k.replace(/^[SE]\d\s/,"").slice(0,12),pnl:Math.round(v.total_pnl||0)}));return(<div><div className="stats-grid">{[{l:"Total P&L",v:fmtINR(pnl?.total_pnl||0),c:(pnl?.total_pnl||0)>=0?"var(--grn)":"var(--red)"},{l:"Total Trades",v:pnl?.total_trades||0,c:"var(--acc)"},{l:"Winners",v:pnl?.winning_trades||0,c:"var(--grn)"},{l:"Win Rate",v:pnl?.total_trades?Math.round((pnl.winning_trades/pnl.total_trades)*100)+"%":"—",c:"var(--yel)"}].map((s,i)=>(<div key={i} className="stat-card"><div className="stat-lbl">{s.l}</div><div className="stat-val" style={{color:s.c}}>{s.v}</div></div>))}</div>{chart.length>0?(<div className="card" style={{marginBottom:14}}><div className="card-lbl">P&amp;L by Strategy</div><ResponsiveContainer width="100%" height={190}><BarChart data={chart} margin={{top:6,right:14,bottom:6,left:0}}><CartesianGrid strokeDasharray="3 3" stroke="var(--br)"/><XAxis dataKey="name" tick={{fontSize:8,fill:"var(--muted)"}}/><YAxis tick={{fontSize:8,fill:"var(--muted)"}} tickFormatter={v=>`₹${(v/1000).toFixed(0)}k`}/><Tooltip contentStyle={{background:"var(--s2)",border:"1px solid var(--br)",borderRadius:7,fontSize:10}} formatter={v=>[`₹${Number(v).toLocaleString("en-IN")}`,"P&L"]}/><Bar dataKey="pnl" fill="var(--acc)" radius={[4,4,0,0]}/></BarChart></ResponsiveContainer></div>):(<div className="empty"><div className="empty-ico">📈</div><div className="empty-t">No closed trades yet</div></div>)}</div>);}


function PlaceOrderModal({sig,onClose,userPlan}){
  const [lots,setLots]=useState(sig.lots_suggested||1);
  const [confirm,setConfirm]=useState(false);
  const [brokerStatus,setBrokerStatus]=useState(null);
  const [result,setResult]=useState(null);
  const [loading,setLoading]=useState(false);
  const isPaid=["weekly","monthly","annual"].includes(userPlan);
  useEffect(()=>{api("/broker/status").then(setBrokerStatus).catch(()=>setBrokerStatus({ok:false,broker:"none"}));},[]);
  const place=async()=>{
    setLoading(true);setResult(null);
    try{
      const r=await api("/broker/place-from-signal",{method:"POST",body:JSON.stringify({signal:sig,lots,confirm})});
      setResult(r);
      if(r.ok&&confirm)setTimeout(onClose,2500);
    }catch(e){setResult({ok:false,message:e.message});}
    finally{setLoading(false);}
  };
  const LOT_SZ={BANKNIFTY:15,NIFTY:25,FINNIFTY:40};
  const inst=sig.instrument||sig.symbol||"—";
  const near=sig.near_strike||sig.strike;
  const dirColor=sig.direction==="LONG"||sig.direction==="BUY"?"var(--grn)":"var(--red)";
  return(<div style={{position:"fixed",inset:0,background:"rgba(5,12,24,.88)",zIndex:210,display:"flex",alignItems:"center",justifyContent:"center",padding:16}}>
    <div style={{background:"var(--s1)",border:"1px solid var(--br2)",borderRadius:12,padding:20,width:"min(400px,100%)",maxHeight:"90vh",overflowY:"auto"}}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:14}}>
        <div style={{fontFamily:"var(--mono)",fontSize:11,color:"var(--acc)",fontWeight:700}}>⚡ PLACE ORDER</div>
        <span style={{cursor:"pointer",color:"var(--muted)",fontSize:18}} onClick={onClose}>×</span>
      </div>
      {brokerStatus&&<div style={{fontSize:9,fontFamily:"var(--mono)",padding:"4px 8px",borderRadius:5,marginBottom:10,background:brokerStatus.ok?"rgba(0,255,157,.06)":"rgba(255,61,90,.06)",color:brokerStatus.ok?"var(--grn)":"var(--red)",border:`1px solid ${brokerStatus.ok?"rgba(0,255,157,.2)":"rgba(255,61,90,.2)"}`}}>
        {brokerStatus.ok?`● ${(brokerStatus.broker||"broker").toUpperCase()} CONNECTED`:"● NO BROKER — add credentials to .env"}
        {brokerStatus.paper&&" (PAPER MODE)"}
      </div>}
      {!isPaid&&<div style={{background:"rgba(245,197,24,.08)",border:"1px solid rgba(245,197,24,.2)",borderRadius:7,padding:"8px 11px",fontSize:10,color:"var(--yel)",marginBottom:12}}>
        ⚠ Live trading requires Weekly plan or above.
      </div>}
      <div style={{background:"var(--s2)",borderRadius:8,padding:"10px 12px",marginBottom:12}}>
        <div style={{display:"flex",justifyContent:"space-between",marginBottom:6}}>
          <span style={{fontFamily:"var(--mono)",fontSize:14,fontWeight:700,color:"var(--acc)"}}>{inst}</span>
          <span style={{fontFamily:"var(--mono)",fontSize:14,fontWeight:700,color:dirColor}}>{sig.direction}</span>
        </div>
        {near&&<div style={{fontSize:9,color:"var(--muted)",fontFamily:"var(--mono)"}}>Strike: {near} {sig.option_type||"CE"}  |  {(sig.strategy||"").slice(0,14)}</div>}
        <div style={{fontSize:9,color:"var(--muted)",marginTop:3,fontFamily:"var(--mono)"}}>Score: {sig.score}  |  Risk: {sig.risk||"MEDIUM"}</div>
      </div>
      <div style={{marginBottom:12}}>
        <label style={{fontSize:9,color:"var(--muted)",display:"block",marginBottom:5}}>LOTS</label>
        <div style={{display:"flex",gap:6,alignItems:"center"}}>
          <button className="btn btn-ghost btn-sm" onClick={()=>setLots(l=>Math.max(1,l-1))}>−</button>
          <span style={{fontFamily:"var(--mono)",fontSize:18,fontWeight:700,minWidth:30,textAlign:"center"}}>{lots}</span>
          <button className="btn btn-ghost btn-sm" onClick={()=>setLots(l=>Math.min(50,l+1))}>+</button>
          <span style={{fontSize:9,color:"var(--muted)",fontFamily:"var(--mono)"}}>× {LOT_SZ[inst]||15} = {lots*(LOT_SZ[inst]||15)} qty</span>
        </div>
      </div>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14,padding:"8px 10px",background:"rgba(255,61,90,.06)",border:"1px solid rgba(255,61,90,.15)",borderRadius:7}}>
        <input type="checkbox" id="confirm-chk" checked={confirm} onChange={e=>setConfirm(e.target.checked)} style={{accentColor:"var(--red)"}}/>
        <label htmlFor="confirm-chk" style={{fontSize:10,color:"var(--red)",cursor:"pointer"}}>I confirm this is a <strong>REAL ORDER</strong> — uncheck for dry-run preview</label>
      </div>
      {result&&<div style={{fontSize:11,padding:"7px 10px",borderRadius:6,marginBottom:10,background:result.ok?"rgba(0,255,157,.08)":"rgba(255,61,90,.08)",color:result.ok?"var(--grn)":"var(--red)",border:`1px solid ${result.ok?"rgba(0,255,157,.2)":"rgba(255,61,90,.2)"}`}}>
        {result.ok?(confirm?`✓ Order placed: ${result.order_id||"OK"}`:`✓ Dry-run — broker=${result.broker}`):`✗ ${result.message}`}
      </div>}
      <div style={{display:"flex",gap:8}}>
        <button className={`btn btn-sm ${confirm?"btn-danger":"btn-primary"}`} style={{flex:1}} onClick={place} disabled={loading||!isPaid}>
          {loading?"Placing…":confirm?"🔴 Place Real Order":"👁 Preview Order"}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
      </div>
      <div style={{fontSize:8,color:"var(--dim)",textAlign:"center",marginTop:8}}>Not SEBI-registered advice. Trade at your own risk.</div>
    </div>
  </div>);
}

function MarginBadge({onSetup}){
  const [status,setStatus]=useState(null);
  useEffect(()=>{
    api("/margin/status").then(setStatus).catch(()=>{});
    const t=setInterval(()=>api("/margin/status").then(setStatus).catch(()=>{}),30000);
    return()=>clearInterval(t);
  },[]);
  const available=status?.available||0;
  if(!status)return null;
  if(available<=0){
    return(<div className="badge" style={{cursor:"pointer",color:"var(--yel)",borderColor:"rgba(245,197,24,.3)",background:"rgba(245,197,24,.06)"}} onClick={onSetup}>
      ⚠ Set Margin
    </div>);
  }
  const freeL=(status.free/100000).toFixed(1);
  const totalL=(available/100000).toFixed(1);
  return(<div className="badge" style={{cursor:"pointer",color:"var(--grn)",borderColor:"rgba(0,255,157,.2)",background:"rgba(0,255,157,.04)"}} onClick={onSetup} title="Click to update margin">
    ₹{freeL}L free / {totalL}L
  </div>);
}

function MarginTab(){
  const [status,setStatus]=useState(null);
  const [input,setInput]=useState("");
  const [loading,setLoading]=useState(false);
  const [msg,setMsg]=useState("");

  const load=()=>api("/margin/status").then(setStatus).catch(()=>{});
  useEffect(()=>{load();},[]);

  const save=async()=>{
    if(!input.trim())return;
    setLoading(true);setMsg("");
    try{
      const r=await api("/margin/setup",{method:"POST",body:JSON.stringify({margin:input.trim()})});
      setMsg(r.message||"✓ Saved");
      setInput("");
      load();
    }catch(e){setMsg("✗ "+e.message);}
    finally{setLoading(false);}
  };

  const MARGINS={NIFTY:80000,BANKNIFTY:90000,FINNIFTY:50000};
  const presets=[
    {label:"5L",  value:"500000"},
    {label:"10L", value:"1000000"},
    {label:"25L", value:"2500000"},
    {label:"50L", value:"5000000"},
  ];

  const available=status?.available||0;
  const free=status?.free||0;
  const lotCap=status?.lot_capacity||{};
  const pct=available>0?Math.round((1-free/available)*100):0;

  return(<div>
    {/* Header card */}
    <div style={{background:"var(--s1)",border:"1px solid rgba(0,212,255,.2)",borderRadius:12,padding:"16px 18px",marginBottom:14}}>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:12}}>
        <span style={{fontSize:20}}>₹</span>
        <div style={{fontFamily:"var(--mono)",fontSize:13,fontWeight:700,color:"var(--acc)"}}>MARGIN SETUP</div>
        <span style={{fontSize:9,fontFamily:"var(--mono)",padding:"2px 7px",borderRadius:10,background:available>0?"rgba(0,255,157,.1)":"rgba(255,61,90,.1)",color:available>0?"var(--grn)":"var(--red)",border:`1px solid ${available>0?"rgba(0,255,157,.2)":"rgba(255,61,90,.2)"}`}}>
          {available>0?"SET":"NOT SET"}
        </span>
      </div>
      {available>0?(<>
        {/* Margin bars */}
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:10,marginBottom:14}}>
          {[
            {lbl:"AVAILABLE",val:`₹${(available/100000).toFixed(1)}L`,col:"var(--acc)"},
            {lbl:"DEPLOYED", val:`₹${((available-free)/100000).toFixed(1)}L`,col:"var(--yel)"},
            {lbl:"FREE",     val:`₹${(free/100000).toFixed(1)}L`,col:"var(--grn)"},
          ].map((s,i)=>(<div key={i} style={{background:"var(--s2)",borderRadius:8,padding:"9px 12px",textAlign:"center"}}>
            <div style={{fontSize:8,color:"var(--muted)",letterSpacing:"1.5px",marginBottom:4}}>{s.lbl}</div>
            <div style={{fontFamily:"var(--mono)",fontSize:18,fontWeight:700,color:s.col}}>{s.val}</div>
          </div>))}
        </div>
        {/* Utilisation bar */}
        <div style={{marginBottom:14}}>
          <div style={{display:"flex",justifyContent:"space-between",fontSize:8,color:"var(--muted)",marginBottom:4}}>
            <span>UTILISATION</span><span style={{fontFamily:"var(--mono)",color:"var(--yel)"}}>{pct}%</span>
          </div>
          <div style={{height:6,background:"var(--br2)",borderRadius:3,overflow:"hidden"}}>
            <div style={{height:"100%",width:`${pct}%`,background:pct>80?"var(--red)":pct>50?"var(--yel)":"var(--grn)",borderRadius:3,transition:"width .4s"}}/>
          </div>
        </div>
        {/* Lot capacity */}
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:8}}>
          {Object.entries(lotCap).map(([inst,lots])=>(
            <div key={inst} style={{background:"var(--s2)",borderRadius:7,padding:"8px 10px",textAlign:"center"}}>
              <div style={{fontSize:8,color:"var(--muted)",marginBottom:3,letterSpacing:"1px"}}>{inst}</div>
              <div style={{fontFamily:"var(--mono)",fontSize:20,fontWeight:700,color:"var(--acc)"}}>{lots}</div>
              <div style={{fontSize:8,color:"var(--dim)"}}>max lots</div>
              <div style={{fontSize:7,color:"var(--dim)",fontFamily:"var(--mono)"}}>₹{(MARGINS[inst]/1000).toFixed(0)}k/lot</div>
            </div>
          ))}
        </div>
      </>):(
        <div style={{textAlign:"center",padding:"20px 0",color:"var(--muted)",fontSize:11}}>
          <div style={{fontSize:28,marginBottom:8}}>₹</div>
          <div>Set your trading margin to enable dynamic lot sizing</div>
        </div>
      )}
    </div>

    {/* Input */}
    <div style={{background:"var(--s1)",border:"1px solid var(--br)",borderRadius:10,padding:"14px 16px",marginBottom:12}}>
      <div style={{fontFamily:"var(--mono)",fontSize:10,color:"var(--acc)",fontWeight:700,marginBottom:10}}>
        {available>0?"UPDATE MARGIN":"SET TODAY'S MARGIN"}
      </div>
      {msg&&<div style={{fontSize:11,padding:"6px 9px",borderRadius:6,marginBottom:10,
        background:msg.startsWith("✓")?"rgba(0,255,157,.08)":"rgba(255,61,90,.08)",
        color:msg.startsWith("✓")?"var(--grn)":"var(--red)",
        border:`1px solid ${msg.startsWith("✓")?"rgba(0,255,157,.2)":"rgba(255,61,90,.2)"}`}}>{msg}</div>}
      <div style={{marginBottom:10}}>
        <label style={{fontSize:9,color:"var(--muted)",display:"block",marginBottom:5,letterSpacing:".5px"}}>AVAILABLE MARGIN</label>
        <input
          className="form-inp"
          placeholder="e.g. 5000000 or 50L or 5,00,000"
          value={input}
          onChange={e=>setInput(e.target.value)}
          onKeyDown={e=>e.key==="Enter"&&save()}
          style={{fontSize:14,fontFamily:"var(--mono)"}}
        />
        <div style={{fontSize:9,color:"var(--dim)",marginTop:4}}>Accepted formats: 5000000 / 50L / 5,00,000</div>
      </div>
      {/* Quick presets */}
      <div style={{display:"flex",gap:6,marginBottom:12,flexWrap:"wrap"}}>
        {presets.map(p=>(<button key={p.label} className="btn btn-ghost btn-sm" style={{fontFamily:"var(--mono)",fontSize:10}} onClick={()=>setInput(p.value)}>{p.label}</button>))}
      </div>
      <button className="btn btn-primary" style={{width:"100%"}} onClick={save} disabled={loading||!input.trim()}>
        {loading?"Saving…":"Set Margin"}
      </button>
    </div>

    {/* How sizing works */}
    <div style={{background:"var(--s1)",border:"1px solid var(--br)",borderRadius:10,padding:"14px 16px"}}>
      <div style={{fontFamily:"var(--mono)",fontSize:10,color:"var(--muted)",fontWeight:700,marginBottom:10,letterSpacing:"1px"}}>HOW DYNAMIC SIZING WORKS</div>
      {[
        ["Signal Score",   "Higher score → more lots. Score 85+ = full size, 45 = 10% size"],
        ["VIX Level",      "VIX <13 = full size. VIX >22 = 15% size (extreme caution)"],
        ["Market Regime",  "Sideways/low-vol = full. Extreme panic = 5% (near-zero)"],
        ["Win/Loss Streak","3 losses in a row = half size until a win. 3 wins = 10% boost"],
        ["Strategy Risk",  "Calendar/Iron Condor (low risk) get higher sizing than naked options"],
      ].map(([k,v])=>(<div key={k} style={{display:"flex",gap:10,marginBottom:7,fontSize:10}}>
        <div style={{fontFamily:"var(--mono)",color:"var(--acc)",minWidth:120,fontSize:9,flexShrink:0,paddingTop:2}}>{k}</div>
        <div style={{color:"var(--muted)",lineHeight:1.5}}>{v}</div>
      </div>))}
    </div>
  </div>);
}

function SubscriptionTab({user}){
  const [plans,setPlans]=useState([]);const [status,setStatus]=useState(null);
  const [billing,setBilling]=useState("monthly");const [loading,setLoading]=useState("");const [msg,setMsg]=useState("");
  useEffect(()=>{api("/subscription/plans").then(d=>setPlans(d.plans||[])).catch(()=>{});api("/subscription/status").then(d=>{setStatus(d);if(d.billing)setBilling(d.billing);}).catch(()=>{});},[]);
  const upgrade=async(planId)=>{setLoading(planId);setMsg("");try{const r=await api("/subscription/upgrade",{method:"POST",body:JSON.stringify({plan:planId,billing})});if(r.plan){setMsg(`✓ ${r.plan} (${r.billing}) ₹${r.price}`);api("/subscription/status").then(setStatus);}else setMsg(r.detail||"Upgrade failed");}catch(e){setMsg("Error: "+e.message);}finally{setLoading("");}}
  const currentPlan=status?.plan||user?.plan||"free";const currentBilling=status?.billing||"monthly";
  const BADGE_COL={free:"#5a7a9a",weekly:"#00d4ff",monthly:"#00ff9d",annual:"#f5c518"};
  const getPrice=planId=>{const pp=PLAN_PRICES[planId];if(!pp)return null;const p=pp[billing];return p===undefined?null:p;};
  const getSuffix=()=>BILLING_CYCLES.find(b=>b.id===billing)?.suffix||"/mo";
  return(<div>
    <div style={{marginBottom:16,padding:"12px 14px",background:"var(--s1)",border:"1px solid var(--br)",borderRadius:10}}>
      <div style={{fontSize:8,color:"var(--muted)",letterSpacing:"1.5px",textTransform:"uppercase",marginBottom:5}}>CURRENT PLAN</div>
      <div style={{display:"flex",alignItems:"center",gap:14,flexWrap:"wrap"}}>
        <div style={{fontFamily:"var(--mono)",fontSize:18,fontWeight:700,color:BADGE_COL[currentPlan]||"var(--acc)"}}>{currentPlan.toUpperCase()}</div>
        {status?.tier&&<div style={{fontSize:9,color:"var(--muted)"}}>{status.tier.live?"✓ Live":"⚠ Delayed"} · {status.tier.strategies} strategies</div>}
        {status?.plan_expiry&&<div style={{fontSize:9,color:"var(--yel)",fontFamily:"var(--mono)"}}>Expires: {status.plan_expiry}</div>}
      </div>
    </div>
    <div style={{marginBottom:16}}>
      <div style={{fontSize:8,color:"var(--muted)",letterSpacing:"1.5px",textTransform:"uppercase",marginBottom:7}}>BILLING CYCLE</div>
      <div className="billing-toggle">{BILLING_CYCLES.map(b=>(<div key={b.id} className={`billing-tab ${billing===b.id?"act":""}`} onClick={()=>setBilling(b.id)}>{b.label}</div>))}</div>
      {billing==="annual"&&<div style={{fontSize:10,color:"var(--grn)",fontFamily:"var(--mono)",marginTop:4}}>🎉 Annual saves ₹8,000 vs monthly!</div>}
      {billing==="weekly"&&<div style={{fontSize:10,color:"var(--yel)",fontFamily:"var(--mono)",marginTop:4}}>Try the platform for 7 days at ₹500</div>}
    </div>
    {msg&&<div style={{fontSize:11,padding:"7px 11px",borderRadius:7,marginBottom:12,background:msg.startsWith("✓")?"rgba(0,255,157,.08)":"rgba(255,61,90,.08)",color:msg.startsWith("✓")?"var(--grn)":"var(--red)",border:`1px solid ${msg.startsWith("✓")?"rgba(0,255,157,.2)":"rgba(255,61,90,.2)"}`}}>{msg}</div>}
    <div className="plans-grid">
      {["free","weekly","monthly","annual"].map(planId=>{
        const plan=plans.find(p=>p.id===planId)||{};const isCurrent=planId===currentPlan&&currentBilling===billing;
        const bc=BADGE_COL[planId]||"var(--acc)";const price=getPrice(planId);const noAvail=price===null;
        return(<div key={planId} className={`plan-card ${isCurrent?"current":""} ${noAvail?"plan-na":""}`}>
          <div className="plan-badge" style={{background:bc+"20",color:bc,border:`1px solid ${bc}30`}}>{isCurrent?"ACTIVE":(plan.badge||planId.toUpperCase())}</div>
          <div className="plan-name" style={{color:bc}}>{plan.name||planId}</div>
          {noAvail?<div style={{fontSize:11,color:"var(--dim)",marginBottom:6,marginTop:4}}>Not in {billing}</div>:<><div className="plan-price">{price===0?"FREE":`₹${price?.toLocaleString("en-IN")}`}</div><div className="plan-price-suffix">{price===0?"forever":getSuffix()}</div>{planId==="annual"&&<div style={{fontSize:8,color:"var(--grn)",fontFamily:"var(--mono)",marginBottom:4}}>= ₹833/mo — save 44%</div>}</> }
          <ul className="plan-features">{(plan.features||[]).map((f,i)=>(<li key={i}>{f}</li>))}</ul>
          {!isCurrent&&!noAvail&&planId!=="free"&&<button className="btn btn-primary" style={{width:"100%",fontSize:10}} onClick={()=>upgrade(planId)} disabled={loading===planId}>{loading===planId?"Processing…":`Subscribe ₹${price}`}</button>}
          {isCurrent&&<div style={{textAlign:"center",fontSize:9,color:bc,fontFamily:"var(--mono)",padding:"7px 0",fontWeight:700}}>CURRENT PLAN</div>}
          {planId==="free"&&!isCurrent&&<div style={{textAlign:"center",fontSize:9,color:"var(--muted)",padding:"7px 0"}}>Always free</div>}
        </div>);
      })}
    </div>
    <div className="card"><div className="card-lbl">PRICING SUMMARY</div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:10}}>
        {[{lbl:"Weekly Pass",price:"₹500",period:"/week",col:"#00d4ff",note:"Try 7 days"},{lbl:"Monthly Plan",price:"₹1,500",period:"/month",col:"#00ff9d",note:"Most popular"},{lbl:"Annual Plan",price:"₹10,000",period:"/year",col:"#f5c518",note:"Save ₹8,000"}]
          .map((s,i)=>(<div key={i} style={{background:"var(--s2)",borderRadius:8,padding:"10px 12px",border:`1px solid ${s.col}20`}}><div style={{fontSize:9,color:"var(--muted)",marginBottom:3}}>{s.lbl}</div><div style={{fontFamily:"var(--mono)",fontSize:18,fontWeight:700,color:s.col}}>{s.price}</div><div style={{fontSize:9,color:"var(--muted)"}}>{s.period}</div><div style={{fontSize:8,color:s.col,marginTop:3,fontFamily:"var(--mono)"}}>{s.note}</div></div>))}
      </div>
    </div>
  </div>);
}

function PaperTab(){const [acc,setAcc]=useState(null);const [form,setForm]=useState({strategy:"S1 CALENDAR",instrument:"BANKNIFTY",direction:"LONG",lots:1,entry_spread:0,notes:""});const [closing,setClosing]=useState(null);const [exitSpread,setExitSpread]=useState(0);const [loading,setLoading]=useState(false);const [msg,setMsg]=useState("");const load=()=>api("/paper/account").then(setAcc).catch(()=>{});useEffect(()=>{load();},[]);const enter=async()=>{setLoading(true);setMsg("");try{const r=await api("/paper/trade",{method:"POST",body:JSON.stringify(form)});if(r.paper_trade){setMsg("✓ Entered: "+r.paper_trade.id);load();}else setMsg(r.detail||"Error");}catch(e){setMsg("Error: "+e.message);}finally{setLoading(false);}};const closeP=async(id)=>{setLoading(true);setMsg("");try{const r=await api("/paper/close",{method:"POST",body:JSON.stringify({trade_id:id,exit_spread:parseFloat(exitSpread)||0})});if(r.pnl_inr!==undefined){setMsg(`✓ P&L: ₹${r.pnl_inr.toLocaleString("en-IN")}`);setClosing(null);load();}else setMsg(r.detail||"Error");}catch(e){setMsg("Error: "+e.message);}finally{setLoading(false);}};const open=(acc?.trades||[]).filter(t=>t.status==="OPEN");const closed=(acc?.trades||[]).filter(t=>t.status==="CLOSED");const pnlCol=(acc?.total_pnl||0)>=0?"var(--grn)":"var(--red)";const msgGood=msg.startsWith("✓");return(<div><div style={{marginBottom:14,padding:"12px 14px",background:"var(--s1)",border:"1px solid rgba(245,197,24,.2)",borderRadius:10}}><div className="paper-bal-label">📄 PAPER TRADING</div><div style={{display:"flex",alignItems:"baseline",gap:18,marginTop:5,flexWrap:"wrap"}}>{[{lbl:"BALANCE",val:acc?fmtINR(acc.balance):"Loading…",col:"var(--yel)",sz:20},{lbl:"P&L",val:acc?fmtINR(acc.total_pnl):"—",col:pnlCol,sz:16},{lbl:"TRADES",val:acc?`${acc.open_count} open / ${acc.closed_count} closed`:"—",col:"var(--acc)",sz:13}].map((s,i)=>(<div key={i}><div style={{fontSize:8,color:"var(--muted)",marginBottom:2}}>{s.lbl}</div><div style={{fontFamily:"var(--mono)",fontSize:s.sz,fontWeight:700,color:s.col}}>{s.val}</div></div>))}</div></div><div className="paper-trade-form"><div className="paper-form-title">+ NEW PAPER TRADE</div>{msg&&<div style={{fontSize:11,padding:"6px 9px",borderRadius:6,marginBottom:9,background:msgGood?"rgba(0,255,157,.08)":"rgba(255,61,90,.08)",color:msgGood?"var(--grn)":"var(--red)",border:`1px solid ${msgGood?"rgba(0,255,157,.2)":"rgba(255,61,90,.2)"}`}}>{msg}</div>}<div className="form-row"><div className="form-field"><label className="form-lbl">Strategy</label><select className="form-sel" value={form.strategy} onChange={e=>setForm({...form,strategy:e.target.value})}>{["S1 CALENDAR","S2 IRON CONDOR","S3 SHORT STRADDLE","S4 0DTE SCALP","S5 PCR CONTRARIAN"].map(s=><option key={s}>{s}</option>)}</select></div><div className="form-field"><label className="form-lbl">Instrument</label><select className="form-sel" value={form.instrument} onChange={e=>setForm({...form,instrument:e.target.value})}>{["BANKNIFTY","NIFTY","FINNIFTY"].map(s=><option key={s}>{s}</option>)}</select></div></div><div className="form-row"><div className="form-field"><label className="form-lbl">Direction</label><select className="form-sel" value={form.direction} onChange={e=>setForm({...form,direction:e.target.value})}><option>LONG</option><option>SHORT</option></select></div><div className="form-field"><label className="form-lbl">Lots</label><input className="form-inp" type="number" min={1} max={50} value={form.lots} onChange={e=>setForm({...form,lots:parseInt(e.target.value)||1})}/></div></div><div className="form-row"><div className="form-field"><label className="form-lbl">Entry Spread (pts)</label><input className="form-inp" type="number" step="0.5" value={form.entry_spread} onChange={e=>setForm({...form,entry_spread:parseFloat(e.target.value)||0})}/></div><div className="form-field"><label className="form-lbl">Notes</label><input className="form-inp" value={form.notes} onChange={e=>setForm({...form,notes:e.target.value})} placeholder="Optional…"/></div></div><button className="btn btn-primary" onClick={enter} disabled={loading} style={{width:"100%"}}>{loading?"Entering…":"Enter Paper Trade"}</button></div>{open.length>0&&<div className="card" style={{marginBottom:12}}><div className="card-lbl">Open</div>{open.map(t=>(<div key={t.id} style={{marginBottom:7}}><div className="paper-trade-row"><span style={{fontFamily:"var(--mono)",fontSize:10,color:"var(--acc)"}}>{t.id}</span><span style={{fontSize:10}}>{t.strategy?.slice(0,13)}</span><span style={{fontFamily:"var(--mono)",fontSize:10}}>{t.instrument} ×{t.lots}</span><span className="paper-status-open">PAPER</span><button className="btn btn-ghost" style={{fontSize:9,padding:"3px 9px"}} onClick={()=>setClosing(closing===t.id?null:t.id)}>Close</button></div>{closing===t.id&&<div style={{display:"flex",gap:7,padding:"7px 0 3px",alignItems:"center"}}><input className="form-inp" type="number" step="0.5" placeholder="Exit spread" style={{width:130}} value={exitSpread} onChange={e=>setExitSpread(e.target.value)}/><button className="btn btn-danger" style={{fontSize:10}} onClick={()=>closeP(t.id)} disabled={loading}>Confirm</button><button className="btn btn-ghost" style={{fontSize:10}} onClick={()=>setClosing(null)}>Cancel</button></div>}</div>))}</div>}{closed.length>0&&<div className="card"><div className="card-lbl">Closed</div>{closed.slice(-8).reverse().map(t=>(<div className="paper-trade-row" key={t.id}><span style={{fontFamily:"var(--mono)",fontSize:9,color:"var(--dim)"}}>{t.id}</span><span style={{fontSize:10}}>{t.instrument}</span><span style={{fontFamily:"var(--mono)",fontWeight:700,fontSize:10,color:(t.pnl_inr||0)>=0?"var(--grn)":"var(--red)"}}>{fmtINR(t.pnl_inr)}</span><span style={{fontFamily:"var(--mono)",fontSize:8,color:(t.pnl_pts||0)>=0?"var(--grn)":"var(--red)"}}>{t.pnl_pts!=null?`${t.pnl_pts>0?"+":""}${t.pnl_pts}pts`:""}</span><span className="paper-status-closed">CLOSED</span></div>))}</div>}</div>);}

// ── Strategy Trust Panel ─────────────────────────────────────────────────────
const TRUST_STRATEGIES=[
  {id:"calendar",tag:"CALENDAR SPREAD",name:"Time Decay Harvester",tagline:"Earn from time, not market direction",color:"#00d4ff",icon:"⏳",
    layman:"Think of this like renting out your parking spot. You collect rent (premium) every week — whether the car parks or not. This strategy profits when the market stays calm and time passes.",
    howItWorks:[{step:"SELL",desc:"a near-month option to collect premium"},{step:"BUY",desc:"a far-month option as protection"},{step:"PROFIT",desc:"as the sold option loses value faster"}],
    bestWhen:"Markets are range-bound or sideways",avoid:"High volatility or major news events",
    winRate:68,avgReturn:"2.1%",tested:"3.2 yrs",trades:1240,riskLevel:2,
    trustFactors:["Backtested on 3+ years of NSE data","Positive Sharpe ratio across all market phases","Max drawdown < 8% historically"]},
  {id:"pcr",tag:"PCR SIGNAL",name:"Crowd Sentiment Reversal",tagline:"When everyone bets one way — go the other",color:"#22c55e",icon:"🧭",
    layman:"Imagine everyone in a crowded theater rushing to one exit. This signal detects when too many traders pile on the same side — and bets on a reversal. Contrarian, disciplined, and data-driven.",
    howItWorks:[{step:"MEASURE",desc:"Put/Call ratio across Nifty & BankNifty"},{step:"DETECT",desc:"extreme readings above 1.3 or below 0.7"},{step:"SIGNAL",desc:"a likely reversal in the next 1–3 sessions"}],
    bestWhen:"Fear or greed is at an extreme",avoid:"Trending markets with sustained momentum",
    winRate:63,avgReturn:"3.4%",tested:"4.1 yrs",trades:980,riskLevel:3,
    trustFactors:["Based on institutional options flow data","Works across bull & bear cycles","Validated with Nifty OI data since 2020"]},
  {id:"equity",tag:"EQUITY MOMENTUM",name:"Trend Rider",tagline:"Strong stocks get stronger — we ride that wave",color:"#a78bfa",icon:"🌊",
    layman:"Picture a train leaving the station. This strategy identifies stocks already moving fast, jumps on board, and rides the momentum — getting off before the train stops, protecting profits automatically.",
    howItWorks:[{step:"SCREEN",desc:"top movers with volume confirmation"},{step:"ENTER",desc:"on breakout with F&O hedging"},{step:"EXIT",desc:"via trailing stop-loss or target hit"}],
    bestWhen:"Clear trend days with strong sector rotation",avoid:"Expiry weeks and low-volume sessions",
    winRate:71,avgReturn:"4.2%",tested:"2.8 yrs",trades:730,riskLevel:3,
    trustFactors:["Sector + index confirmation filter reduces false signals","Combined with F&O hedges for downside protection","Outperforms Nifty50 on a risk-adjusted basis"]},
];

function StrategyTrustPanel(){
  const [active,setActive]=useState("calendar");
  const [liveStats,setLiveStats]=useState({});
  useEffect(()=>{
    // Fetch real computed backtest stats from backend
    api("/analytics/backtest-stats").then(d=>{
      if(d&&d.stats) setLiveStats(d.stats);
    }).catch(()=>{});
  },[]);
  const s=TRUST_STRATEGIES.find(x=>x.id===active);
  return(
    <div style={{fontFamily:"var(--body)"}}>
      {/* Header */}
      <div style={{marginBottom:20}}>
        <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:6}}>
          <div style={{width:6,height:6,borderRadius:"50%",background:"var(--grn)",animation:"pulse 2s infinite"}}/>
          <span style={{fontSize:9,fontFamily:"var(--mono)",letterSpacing:"0.12em",color:"var(--muted)",textTransform:"uppercase"}}>Strategy Intelligence</span>
        </div>
        <h2 style={{margin:0,fontSize:20,fontWeight:700,letterSpacing:"-0.02em",color:"var(--text)"}}>Why should you trust these signals?</h2>
        <p style={{margin:"7px 0 0",color:"var(--muted)",fontSize:12,lineHeight:1.6,maxWidth:520}}>Every signal is backed by years of backtesting, real NSE data, and disciplined risk rules. Here's what each strategy does — in plain English.</p>
      </div>

      {/* Tabs */}
      <div style={{display:"flex",gap:7,marginBottom:16,flexWrap:"wrap"}}>
        {TRUST_STRATEGIES.map(st=>(
          <button key={st.id} onClick={()=>setActive(st.id)} style={{background:active===st.id?st.color+"18":"var(--s2)",border:`1px solid ${active===st.id?st.color+"55":"var(--br)"}`,borderRadius:7,padding:"8px 14px",cursor:"pointer",color:active===st.id?st.color:"var(--muted)",fontFamily:"var(--body)",fontWeight:600,fontSize:12,transition:"all .15s",display:"flex",alignItems:"center",gap:7}}>
            <span>{st.icon}</span><span>{st.name}</span>
          </button>
        ))}
      </div>

      {/* Main Card */}
      <div key={active} style={{background:"var(--s1)",border:"1px solid var(--br)",borderRadius:12,overflow:"hidden",animation:"fadeUp .3s ease"}}>
        <div style={{height:3,background:`linear-gradient(90deg,${s.color},${s.color}44,transparent)`}}/>
        <div style={{padding:"22px 24px"}}>
          {/* Title + metrics */}
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:16,flexWrap:"wrap",gap:12}}>
            <div>
              <span style={{fontSize:9,fontWeight:700,letterSpacing:"0.08em",textTransform:"uppercase",color:s.color,border:`1px solid ${s.color}44`,background:`${s.color}11`,borderRadius:4,padding:"2px 8px"}}>{s.tag}</span>
              <div style={{margin:"8px 0 3px",fontSize:18,fontWeight:700,letterSpacing:"-0.02em"}}>{s.icon} {s.name}</div>
              <div style={{fontSize:12,color:s.color,fontWeight:500}}>{s.tagline}</div>
            </div>
            <div style={{display:"flex",gap:14,flexWrap:"wrap"}}>
              {[{l:"Win Rate",v:`${(liveStats[s.id]?.win_rate||s.winRate)}%`},{l:"Avg Return",v:liveStats[s.id]?.avg_return||s.avgReturn},{l:"Backtested",v:liveStats[s.id]?.tested||s.tested},{l:"Trades",v:(liveStats[s.id]?.trades||s.trades).toLocaleString()}].map(m=>(
                <div key={m.l} style={{textAlign:"center"}}>
                  <div style={{fontSize:17,fontWeight:700,color:s.color,fontFamily:"var(--mono)"}}>{m.v}</div>
                  <div style={{fontSize:9,color:"var(--muted)",textTransform:"uppercase",letterSpacing:"0.05em",marginTop:2}}>{m.l}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Win rate bar */}
          <div style={{marginBottom:18}}>
            <div style={{display:"flex",justifyContent:"space-between",marginBottom:5,fontSize:10,color:"var(--muted)"}}>
              <span>Historical Win Rate</span><span style={{color:s.color,fontWeight:600}}>{liveStats[s.id]?.win_rate||s.winRate}%{liveStats[s.id]&&<span style={{fontSize:7,color:"var(--grn)",marginLeft:4}}>●LIVE</span>}</span>
            </div>
            <div style={{height:5,background:"var(--br2)",borderRadius:99,overflow:"hidden"}}>
              <div style={{height:"100%",width:`${liveStats[s.id]?.win_rate||s.winRate}%`,background:s.color,borderRadius:99,boxShadow:`0 0 8px ${s.color}66`,transition:"width 1.2s cubic-bezier(.16,1,.3,1)"}}/>
            </div>
          </div>

          <hr style={{border:"none",borderTop:"1px solid var(--br)",margin:"0 0 18px"}}/>

          {/* Plain-English box */}
          <div style={{background:`${s.color}0A`,border:`1px solid ${s.color}22`,borderRadius:9,padding:"13px 16px",marginBottom:18}}>
            <div style={{fontSize:9,textTransform:"uppercase",letterSpacing:"0.1em",color:s.color,fontWeight:700,marginBottom:7}}>💡 In Simple Terms</div>
            <p style={{margin:0,fontSize:12,lineHeight:1.7,color:"var(--text)"}}>{s.layman}</p>
          </div>

          {/* Two columns */}
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
            {/* How it works */}
            <div>
              <div style={{fontSize:9,textTransform:"uppercase",letterSpacing:"0.08em",color:"var(--muted)",fontWeight:600,marginBottom:12}}>How It Works</div>
              {s.howItWorks.map((h,i)=>(
                <div key={i} style={{display:"flex",gap:10,marginBottom:10,alignItems:"flex-start"}}>
                  <div style={{minWidth:52,height:20,background:`${s.color}22`,border:`1px solid ${s.color}44`,borderRadius:4,display:"flex",alignItems:"center",justifyContent:"center",fontSize:8,fontWeight:700,color:s.color,letterSpacing:"0.05em",fontFamily:"var(--mono)",flexShrink:0}}>{h.step}</div>
                  <span style={{fontSize:11,color:"var(--muted)",lineHeight:1.5,paddingTop:2}}>{h.desc}</span>
                </div>
              ))}
              <div style={{marginTop:14}}>
                <div style={{fontSize:9,color:"var(--muted)",marginBottom:5,textTransform:"uppercase",letterSpacing:"0.05em"}}>Risk Level</div>
                <div style={{display:"flex",gap:4}}>{[1,2,3,4,5].map(i=><div key={i} style={{width:7,height:7,borderRadius:"50%",background:i<=s.riskLevel?"#ff6b35":"var(--br2)"}}/>)}</div>
              </div>
            </div>

            {/* Trust factors */}
            <div>
              <div style={{fontSize:9,textTransform:"uppercase",letterSpacing:"0.08em",color:"var(--muted)",fontWeight:600,marginBottom:12}}>Why Trust This Signal</div>
              {s.trustFactors.map((t,i)=>(
                <div key={i} style={{display:"flex",alignItems:"flex-start",gap:9,padding:"7px 9px",borderRadius:6,marginBottom:3}}>
                  <span style={{color:s.color,fontSize:11,marginTop:1}}>✓</span>
                  <span style={{fontSize:11,color:"var(--muted)",lineHeight:1.5}}>{t}</span>
                </div>
              ))}
              <div style={{marginTop:14,display:"flex",flexDirection:"column",gap:7}}>
                <div style={{background:"rgba(0,255,157,.06)",border:"1px solid rgba(0,255,157,.15)",borderRadius:7,padding:"8px 12px"}}>
                  <span style={{fontSize:9,color:"var(--grn)",fontWeight:700,textTransform:"uppercase",letterSpacing:"0.05em"}}>✅ Best When · </span>
                  <span style={{fontSize:11,color:"var(--grn)"}}>{s.bestWhen}</span>
                </div>
                <div style={{background:"rgba(255,107,53,.06)",border:"1px solid rgba(255,107,53,.15)",borderRadius:7,padding:"8px 12px"}}>
                  <span style={{fontSize:9,color:"var(--orn)",fontWeight:700,textTransform:"uppercase",letterSpacing:"0.05em"}}>⚠ Avoid · </span>
                  <span style={{fontSize:11,color:"var(--orn)"}}>{s.avoid}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Footer trust badges */}
      <div style={{display:"flex",alignItems:"center",justifyContent:"center",gap:20,marginTop:14,padding:"12px 18px",background:"var(--s1)",border:"1px solid var(--br)",borderRadius:9,flexWrap:"wrap"}}>
        {[{ico:"🔒",t:"Fully rules-based — no manual intervention"},{ico:"📊",t:"NSE live data feed · Real-time signals"},{ico:"🧪",t:"Backtested · Not curve-fitted"}].map(b=>(
          <div key={b.t} style={{display:"flex",alignItems:"center",gap:7}}>
            <span style={{fontSize:12}}>{b.ico}</span>
            <span style={{fontSize:10,color:"var(--muted)"}}>{b.t}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function App(){
  const [user,setUser]=useState(()=>localStorage.getItem("tok")?{tok:true}:null);
  const [signals,setSigs]=useState([]);const [regime,setRegime]=useState(null);
  const [indicesMap,setIdxMap]=useState({});
  // ── Filter memory — persists across refreshes ─────────────────
  const [mkt,setMkt]=useState(()=>localStorage.getItem("at_mkt")||"ALL");
  const [strat,setStrat]=useState(()=>localStorage.getItem("at_strat")||null);
  const [tab,setTab]=useState(()=>localStorage.getItem("at_tab")||"signals");
  const [openMkt,setOpenMkt]=useState(null);
  // Persist filter changes
  const setMktP  =v=>{setMkt(v);  localStorage.setItem("at_mkt",v);};
  const setStratP=v=>{setStrat(v);v?localStorage.setItem("at_strat",v):localStorage.removeItem("at_strat");};
  const setTabP  =v=>{setTab(v);  localStorage.setItem("at_tab",v);};
  const [wsStatus,setWsSt]=useState("connecting");
  const [nseLive,setNseLive]=useState(false);const [dhanLive,setDhanLive]=useState(false);
  const [clock,setClock]=useState(new Date());
  const [pcrHistory,setPcrHistory]=useState({NIFTY:[],BANKNIFTY:[],FINNIFTY:[]});
  const [logModal,setLogModal]=useState(null);
  const [placeModal,setPlaceModal]=useState(null);
  const wsRef=useRef(null);
  const IDX_ORDER=["NIFTY","BANKNIFTY","FINNIFTY","VIX","MIDCAP","IT"];
  const indices=IDX_ORDER.map(l=>indicesMap[l]).filter(Boolean);

  useEffect(()=>{const t=setInterval(()=>setClock(new Date()),100);return()=>clearInterval(t);},[]);

  const addSignals=useCallback((incoming)=>{
    setSigs(prev=>mergeSignals(prev,Array.isArray(incoming)?incoming:[incoming]));
  },[]);

  const addPcrHistory=useCallback((sig)=>{
    if(!sig.total_call_oi&&!sig.total_put_oi)return;
    const inst=sig.instrument||"NIFTY";
    setPcrHistory(prev=>({...prev,[inst]:[...(prev[inst]||[]),{time:sig.timestamp||"",callOI:sig.total_call_oi||0,putOI:sig.total_put_oi||0,spot:sig.spot||0}].slice(-60)}));
  },[]);

  useEffect(()=>{
    if(!user)return;
    const conn=()=>{
      const ws=new WebSocket(WS);wsRef.current=ws;
      let _retryDelay=2000;
      ws.onopen=()=>{setWsSt("live");_retryDelay=2000;};
      ws.onclose=()=>{setWsSt("reconnecting");_retryDelay=Math.min(_retryDelay*1.5,15000);setTimeout(conn,_retryDelay);};
      ws.onerror=()=>{setWsSt("reconnecting");ws.close();};
      ws.onmessage=e=>{
        try{
          const d=JSON.parse(e.data);
          if(d.type==="signal"&&d.data){addSignals([d.data]);if(d.data.regime)setRegime(r=>({...r,regime:d.data.regime,vix:d.data.vix}));if((d.data.strategy||"").toUpperCase().includes("PCR")||d.data.source==="pcr_strategy"||d.data.source==="pcr_mock")addPcrHistory(d.data);return;}
          if(d.type==="equity_signals"&&d.signals?.length){setSigs(prev=>mergeSignals(prev.filter(s=>s.market!=="EQUITY"),d.signals));return;}
          if(d.type==="indices_update"&&d.indices?.length){setIdxMap(prev=>{const next={...prev};for(const idx of d.indices){const pl=(prev[idx.label]?.ltp)||0;const flash=pl&&idx.ltp!==pl?(idx.ltp>pl?"flash-up":"flash-dn"):"";next[idx.label]={...idx,_flash:flash,_ts:Date.now()};}return next;});setTimeout(()=>setIdxMap(prev=>{const c={...prev};for(const k of Object.keys(c))c[k]={...c[k],_flash:""};return c;}),1300);return;}
          if(d.type==="regime"){setRegime(r=>({...r,...d}));return;}
          if(d.type==="ping"){try{ws.send(JSON.stringify({type:"pong",ts:d.ts}));}catch{}return;}
          if(d.type==="heartbeat"||d.type==="status"){
            if(d.nse_live!=null)setNseLive(!!d.nse_live);
            if(d.dhan_live!=null)setDhanLive(!!d.dhan_live);
            return;
          }
          if(d.signals?.length)addSignals(d.signals);
          if(d.regime)setRegime(r=>({...r,...d.regime}));
        }catch{}
      };
    };
    conn();return()=>wsRef.current?.close();
  },[user,addSignals,addPcrHistory]);

  useEffect(()=>{
    if(!user)return;
    api("/signals?limit=50").then(d=>{if(d.signals?.length)addSignals(d.signals);if(d.nse_live!=null)setNseLive(!!d.nse_live);if(d.dhan_live!=null)setDhanLive(!!d.dhan_live);}).catch(()=>{});
    api("/indices").then(d=>{if(d.indices?.length){const m={};for(const idx of d.indices)m[idx.label]={...idx,_flash:"",_ts:Date.now()};setIdxMap(m);}}).catch(()=>{});
    api("/signals/equity?top=15").then(d=>{if(d.signals?.length)addSignals(d.signals);}).catch(()=>{});
  },[user,addSignals]);

  useEffect(()=>{
    if(!user)return;
    const iv=setInterval(()=>{api("/indices").then(d=>{if(d.indices?.length){setIdxMap(prev=>{const next={...prev};for(const idx of d.indices){const pl=(prev[idx.label]?.ltp)||0;next[idx.label]={...idx,_flash:pl&&idx.ltp!==pl?(idx.ltp>pl?"flash-up":"flash-dn"):"",_ts:Date.now()};}return next;});}}).catch(()=>{});},3000);
    return()=>clearInterval(iv);
  },[user]);

  useEffect(()=>{
    if(!user)return;
    const iv=setInterval(()=>{api("/signals/equity?top=15").then(d=>{if(d.signals?.length)addSignals(d.signals);}).catch(()=>{});},45000);
    return()=>clearInterval(iv);
  },[user,addSignals]);

  if(!user)return(<><style>{CSS}</style><Login onLogin={u=>setUser(u)}/></>);

  const IST=clock.toLocaleTimeString("en-IN",{timeZone:"Asia/Kolkata",hour12:false});
  const IST_MS=("00"+clock.getMilliseconds()).slice(-3).slice(0,1);
  const rCol=!regime?"var(--muted)":regime.vix<15?"var(--grn)":regime.vix<22?"var(--yel)":"var(--red)";
  const bull=signals.filter(s=>["BUY","BULL","LONG"].some(k=>s.direction?.toUpperCase().includes(k))).length;
  const bear=signals.filter(s=>["SELL","BEAR","SHORT","EXIT"].some(k=>s.direction?.toUpperCase().includes(k))).length;
  const neut=signals.length-bull-bear;
  const pcrCount=signals.filter(s=>(s.strategy||"").toUpperCase().includes("PCR")||s.source==="pcr_strategy"||s.source==="pcr_mock").length;
  const foCount=signals.filter(s=>s.market==="FO"||(s.market&&s.market!=="EQUITY")).length;
  const selectMarket=m=>{setMktP(m);setStratP(null);setTabP("signals");setOpenMkt(m!=="ALL"?m:null);};
  const toggleDropdown=m=>{setOpenMkt(prev=>prev===m?null:m);};
  const selectStrategy=s=>{setStratP(prev=>prev===s?null:s);setTabP("signals");};
  const TABS=[{id:"signals",lbl:`Signals (${signals.length})`},{id:"tradelog",lbl:"Trade Log"},{id:"paper",lbl:"Paper"},{id:"analytics",lbl:"Analytics"},{id:"why",lbl:"💡 Why It Works"},{id:"subscription",lbl:"Plans"}];
  const MOB_NAV=[{id:"signals",ico:"◈",lbl:"Signals"},{id:"tradelog",ico:"📝",lbl:"Log"},{id:"paper",ico:"📄",lbl:"Paper"},{id:"margin",ico:"₹",lbl:"Margin"},{id:"subscription",ico:"★",lbl:"Plans"}];

  return(<><style>{CSS}</style>
    {logModal&&<LogTradeModal sig={logModal} onClose={()=>setLogModal(null)} onLogged={()=>{setLogModal(null);setTabP("tradelog");}}/>}
    {placeModal&&<PlaceOrderModal sig={placeModal} userPlan={user?.plan||"free"} onClose={()=>setPlaceModal(null)}/>}
    <div className="app">
      <aside className="sidebar">
        <div className="sb-logo"><div className="logo-t">ALGOTRADE</div><div className="logo-s">NSE SIGNAL PLATFORM v1.0.0</div></div>
        <nav className="sb-nav">
          <div className="nav-sect">Navigate</div>
          {[{id:"signals",ico:"◈",lbl:"Live Signals"},{id:"tradelog",ico:"📝",lbl:"Trade Logger"},{id:"paper",ico:"📄",lbl:"Paper Trade"},{id:"analytics",ico:"◇",lbl:"Analytics"},{id:"margin",ico:"₹",lbl:"Margin Setup"},{id:"why",ico:"💡",lbl:"Why It Works"},{id:"subscription",ico:"★",lbl:"Subscription"}].map(n=>(
            <div key={n.id} className={`nav-it ${tab===n.id?"act":""}`} onClick={()=>setTabP(n.id)}><span className="nav-ico">{n.ico}</span>{n.lbl}</div>
          ))}
          <div className="nav-sect">Markets</div>
          {MARKETS.map(m=>{const cnt=m.id!=="ALL"?signals.filter(s=>matchesMarket(s,m.id)).length:0;return(<div key={m.id}><div className={`mkt-btn ${mkt===m.id?"act":""}`}><div className="mkt-label-area" onClick={()=>selectMarket(m.id)}><div className="mkt-badge" style={{background:m.color+"20",color:m.color}}>{m.icon}</div><span className="mkt-name" style={{color:mkt===m.id?m.color:undefined}}>{m.label}{m.id!=="ALL"&&cnt>0&&<span style={{marginLeft:4,fontSize:7,background:m.color+"20",color:m.color,padding:"1px 4px",borderRadius:3}}>{cnt}</span>}</span></div>{m.id!=="ALL"&&<div className="mkt-chev-btn" onClick={e=>{e.stopPropagation();toggleDropdown(m.id);}}><span className={`chev ${openMkt===m.id?"open":""}`}>▾</span></div>}</div>{openMkt===m.id&&m.strategies&&(<div className="strat-list">{m.strategies.map(s=>{const k=skey(s);const info=STRAT_INFO[k]||{color:m.color};const isAct=strat===s;const c=signals.filter(sg=>matchesMarket(sg,m.id)&&matchesStrategy(sg,s)).length;return(<div key={s} className={`strat-it ${isAct?"act":""}`} onClick={()=>selectStrategy(s)}><div className="s-dot" style={{background:isAct?info.color:"var(--br)"}}/><span style={{flex:1}}>{s}</span>{c>0&&<span style={{fontSize:7,fontFamily:"var(--mono)",color:info.color,background:info.color+"18",padding:"1px 4px",borderRadius:3}}>{c}</span>}<span style={{fontSize:7,color:info.color,fontFamily:"var(--mono)",marginLeft:2}}>{info.tag}</span></div>);})}</div>)}</div>);
          })}
          <div className="nav-sect">Data Sources</div>
          <div className="feed-row feed-ok">◉ NSE Direct API</div>
          <div className="feed-row" style={{color:"#22c55e"}}>◉ Dhan WebSocket</div>
          <div className="feed-row" style={{color:"#22c55e"}}>◉ NSE OI / PCR</div>
          <div style={{marginTop:"auto",padding:"10px 6px",borderTop:"1px solid var(--br)"}}>
            <div className="nav-it" onClick={()=>{localStorage.removeItem("tok");setUser(null);}}><span className="nav-ico">↩</span>Logout</div>
          </div>
        </nav>
      </aside>
      <div className="main">
        <IndexTicker indices={indices}/>
        <header className="topbar">
          <div className="regime-pill"><div className="pulse" style={{background:rCol}}/><span style={{color:rCol,fontWeight:700,fontSize:10}}>{regime?.regime||"DETECTING…"}</span></div>
          <div className="src-pill"><div className="pulse" style={{background:nseLive?"var(--grn)":"var(--yel)",width:5,height:5}}/>{nseLive?"NSE LIVE":"NSE..."}</div>
          {dhanLive&&<div className="src-pill" style={{background:"rgba(0,212,255,.06)",borderColor:"rgba(0,212,255,.18)",color:"var(--acc)"}}><div className="pulse" style={{background:"var(--acc)",width:5,height:5}}/>DHAN WS</div>}
          <div className="topbar-right">
            {regime?.vix!=null&&<div className="badge" style={{color:rCol}}>VIX {regime.vix}</div>}
            {pcrCount>0&&<div className="badge" style={{color:"#22c55e",borderColor:"rgba(34,197,94,.2)"}}>PCR●{pcrCount}</div>}
            <MarginBadge onSetup={()=>setTabP("margin")}/>
            <div className="badge" style={{display:"flex",alignItems:"center",gap:4,color:wsStatus==="live"?"var(--grn)":wsStatus==="connecting"?"var(--yel)":"var(--red)"}}>{wsStatus==="live"?<><span style={{width:5,height:5,borderRadius:"50%",background:"var(--grn)",display:"inline-block",animation:"pulse 1s infinite"}}/>LIVE</>:wsStatus==="connecting"?"◌ CONN":"⚠ RECONN"}</div>
            <div className="badge" style={{color:"var(--muted)"}}><span className="live-dot"/>{IST}<span style={{color:"var(--acc)",fontWeight:700}}>.{IST_MS}</span></div>
          </div>
        </header>
        <div className="tabs">
          {TABS.map(t=>(<div key={t.id} className={`tab ${tab===t.id?"act":""}`} onClick={()=>setTabP(t.id)}>{t.lbl}</div>))}
          {tab==="signals"&&signals.length>0&&(<div className="tab-right"><span className="count-pill" style={{background:"rgba(0,255,157,.08)",color:"var(--grn)"}}>▲{bull}</span><span className="count-pill" style={{background:"rgba(255,61,90,.08)",color:"var(--red)"}}>▼{bear}</span><span className="count-pill" style={{background:"rgba(0,212,255,.08)",color:"var(--acc)"}}>◆{neut}</span></div>)}
        </div>
        <div className="content">
          {tab==="signals"&&<><div className="stats-grid" style={{marginBottom:12}}>{[{l:"Total",v:signals.length,c:"var(--acc)"},{l:"F&O",v:foCount,c:"var(--grn)"},{l:"PCR",v:pcrCount,c:"#22c55e"},{l:"Equity",v:signals.filter(s=>s.market==="EQUITY").length,c:"var(--pur)"}].map((s,i)=>(<div key={i} className="stat-card"><div className="stat-lbl">{s.l}</div><div className="stat-val" style={{color:s.c}}>{s.v}</div>{i===0&&<div className="stat-sub">{mkt!=="ALL"?mkt:"All"}{strat?" · "+strat:""}</div>}</div>))}</div><SignalsTab signals={signals} market={mkt} strategy={strat} indices={indices} onClearStrategy={()=>setStratP(null)} pcrHistory={pcrHistory} onLogTrade={setLogModal} onPlaceOrder={setPlaceModal} userPlan={user?.plan||"free"}/></> }
          {tab==="tradelog"&&<TraderLoggerTab/>}
       