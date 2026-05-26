import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";

const API = "http://localhost:8000";
const WS  = "ws://localhost:8000/ws/signals";

// ─── Sidebar navigation definition ──────────────────────────────
const SIDEBAR_NAV = [
  {
    id: "EQUITY_INDIA",
    label: "Equity India",
    icon: "🇮🇳",
    color: "#4ade80",
    source: "NSE + Delta Exchange",
    desc: "Directional single-side trades on NIFTY/BANKNIFTY",
    strategies: [
      { id: "E1", label: "EMA Momentum",   risk: "MED",  tag:"EMA CROSSOVER" },
      { id: "E2", label: "VWAP Reversion", risk: "MED",  tag:"VWAP" },
      { id: "E3", label: "Opening Range",  risk: "MED",  tag:"ORB" },
      { id: "E4", label: "Trend Follow",   risk: "MED",  tag:"TREND" },
      { id: "E5", label: "Funding Arb",    risk: "LOW",  tag:"FUNDING" },
      { id: "E6", label: "Gap Fill",       risk: "MED",  tag:"GAP" },
      { id: "E7", label: "S/R Levels",     risk: "LOW",  tag:"S/R" },
    ],
  },
  {
    id: "NIFTY_FO",
    label: "NIFTY F&O",
    icon: "₹",
    color: "#34d399",
    source: "NSE + MultiTrade",
    desc: "NSE Nifty 50 options strategies",
    strategies: [
      { id: "S1", label: "Calendar Spread", risk: "LOW",  tag:"CALENDAR" },
      { id: "S2", label: "Iron Condor",     risk: "LOW",  tag:"IRON CONDOR" },
      { id: "S3", label: "Short Straddle",  risk: "MED",  tag:"STRADDLE" },
      { id: "S6", label: "Expiry 0DTE",     risk: "HIGH", tag:"0DTE" },
    ],
  },
  {
    id: "BANKNIFTY_FO",
    label: "BANKNIFTY F&O",
    icon: "🏦",
    color: "#fbbf24",
    source: "NSE + MultiTrade",
    desc: "NSE BankNifty options strategies",
    strategies: [
      { id: "S1", label: "Calendar Spread", risk: "LOW",  tag:"CALENDAR" },
      { id: "S2", label: "Iron Condor",     risk: "LOW",  tag:"IRON CONDOR" },
      { id: "S3", label: "Short Straddle",  risk: "MED",  tag:"STRADDLE" },
      { id: "S6", label: "Expiry 0DTE",     risk: "HIGH", tag:"0DTE" },
    ],
  },
  {
    id: "CRYPTO",
    label: "Crypto Market",
    icon: "₿",
    color: "#fb923c",
    source: "Delta Exchange",
    desc: "BTC, ETH, SOL 24/7 perpetual futures",
    strategies: [
      { id: "CM", label: "Momentum",    risk: "MED",  tag:"MOMENTUM" },
      { id: "FA", label: "Funding Arb", risk: "LOW",  tag:"FUNDING ARB" },
      { id: "CS", label: "Scalp",       risk: "HIGH", tag:"SCALP" },
    ],
  },
  { id: "PAPER",     label: "Paper Trade",  icon: "🎯", color: "#a78bfa", strategies: [] },
  { id: "PLANS",     label: "Subscription", icon: "💎", color: "#f472b6", strategies: [] },
  { id: "ANALYTICS", label: "Analytics",    icon: "📊", color: "#64748b", strategies: [] },
];

const MARGIN_NAV_ID = "MARGIN";

const RISK_C = {
  LOW:  { color:"#34d399", bg:"rgba(52,211,153,.12)", border:"rgba(52,211,153,.3)" },
  MED:  { color:"#fbbf24", bg:"rgba(251,191,36,.12)",  border:"rgba(251,191,36,.3)" },
  HIGH: { color:"#f87171", bg:"rgba(248,113,113,.12)", border:"rgba(248,113,113,.3)" },
};

const PRESETS = [
  { label:"5L",   value:500_000 },
  { label:"10L",  value:1_000_000 },
  { label:"25L",  value:2_500_000 },
  { label:"50L",  value:5_000_000 },
  { label:"1 Cr", value:10_000_000 },
];

const fmtMargin = n =>
  n >= 10_000_000 ? `₹${(n/10_000_000).toFixed(1)} Cr`
  : n >= 100_000  ? `₹${(n/100_000).toFixed(0)}L`
  : `₹${n.toLocaleString("en-IN")}`;

// ─── Global styles ───────────────────────────────────────────────
const G = () => (
  <style>{`
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@700;800&display=swap');
    *, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
    :root {
      --bg:#080d19; --bg2:#0f1729; --bg3:#162035;
      --border:rgba(255,255,255,.07); --border2:rgba(255,255,255,.13);
      --text:#dde6f5; --muted:#4a6080; --dim:#2a3d58;
      --accent:#38bdf8; --green:#34d399; --red:#f87171;
      --yellow:#fbbf24; --orange:#fb923c; --purple:#a78bfa;
      --mono:'JetBrains Mono',monospace; --head:'Syne',sans-serif;
      --sb-w:196px;
    }
    html,body,#root { height:100%; width:100%; overflow:hidden; background:var(--bg); color:var(--text); font-family:var(--mono); font-size:12px; }
    ::-webkit-scrollbar { width:3px; height:3px; }
    ::-webkit-scrollbar-track { background:var(--bg2); }
    ::-webkit-scrollbar-thumb { background:var(--border2); border-radius:2px; }

    /* ── TRUE FULL-SCREEN LAYOUT ── */
    .app { position:fixed; inset:0; display:flex; overflow:hidden; }
    .sidebar { width:var(--sb-w); min-width:var(--sb-w); background:var(--bg2); border-right:1px solid var(--border); display:flex; flex-direction:column; overflow:hidden; flex-shrink:0; }
    .main { flex:1; width:calc(100% - var(--sb-w)); display:flex; flex-direction:column; overflow:hidden; }
    @media(max-width:640px){ .sidebar{ display:none; } .main{ width:100%; } }

    /* ── Sidebar ── */
    .sb-head { padding:11px 12px 8px; border-bottom:1px solid var(--border); flex-shrink:0; }
    .sb-logo { font-family:var(--head); font-size:15px; font-weight:800; background:linear-gradient(135deg,#38bdf8,#34d399); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; }
    .sb-sub  { font-size:8px; color:var(--muted); letter-spacing:.05em; margin-top:1px; }
    .sb-scroll { flex:1; overflow-y:auto; padding:5px 0; min-height:0; }
    .sb-scroll::-webkit-scrollbar { display:none; }
    .sb-item { display:flex; align-items:center; gap:7px; padding:6px 11px; cursor:pointer; transition:.1s; border-left:2px solid transparent; white-space:nowrap; }
    .sb-item:hover { background:rgba(255,255,255,.04); }
    .sb-item.active { background:rgba(56,189,248,.08); border-left-color:var(--accent); }
    .sb-icon { width:16px; text-align:center; font-size:11px; flex-shrink:0; }
    .sb-label { font-size:10px; font-weight:700; flex:1; overflow:hidden; text-overflow:ellipsis; }
    .sb-badge { font-size:7px; font-weight:700; border-radius:10px; padding:1px 5px; flex-shrink:0; }
    .sb-strat { display:flex; align-items:center; gap:6px; padding:4px 11px 4px 30px; cursor:pointer; transition:.1s; }
    .sb-strat:hover { background:rgba(255,255,255,.03); }
    .sb-strat.active { background:rgba(56,189,248,.05); }
    .sb-strat-label { font-size:9px; color:var(--muted); flex:1; }
    .sb-strat.active .sb-strat-label { color:var(--accent); font-weight:700; }
    .sb-divider { height:1px; background:var(--border); margin:3px 11px; }
    .sb-footer { padding:7px 11px; border-top:1px solid var(--border); flex-shrink:0; }
    .sb-margin-pill { display:flex; align-items:center; justify-content:space-between; padding:5px 9px; background:rgba(56,189,248,.07); border:1px solid rgba(56,189,248,.18); border-radius:5px; cursor:pointer; margin:4px 11px 2px; }

    /* ── Topbar ── */
    .topbar { height:38px; background:var(--bg2); border-bottom:1px solid var(--border); display:flex; align-items:center; gap:6px; padding:0 12px; flex-shrink:0; overflow:hidden; }
    .tb-pill { background:var(--bg3); border:1px solid var(--border2); border-radius:20px; padding:2px 8px; font-size:9px; display:flex; align-items:center; gap:3px; white-space:nowrap; flex-shrink:0; }
    .live-dot { width:5px; height:5px; border-radius:50%; background:var(--green); animation:blink 1.4s ease infinite; flex-shrink:0; }
    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.2} }

    /* ── Page — fills all remaining height ── */
    .page { flex:1; overflow-y:auto; overflow-x:hidden; min-height:0; }
    .page-inner { padding:13px 16px; }
    .page-head { margin-bottom:12px; }
    .page-title { font-family:var(--head); font-size:17px; font-weight:800; margin-bottom:2px; }
    .page-sub { font-size:9px; color:var(--muted); }

    /* ── Cards ── */
    .card { background:var(--bg2); border:1px solid var(--border); border-radius:7px; }
    .card-pad { padding:11px 13px; }

    /* ── Stats — 4 cols → 2 cols on small ── */
    .stats-row { display:grid; grid-template-columns:repeat(4,1fr); gap:7px; margin-bottom:12px; }
    @media(max-width:900px){ .stats-row{ grid-template-columns:1fr 1fr; } }
    .stat-card { background:var(--bg2); border:1px solid var(--border); border-radius:6px; padding:9px 10px; }
    .stat-lbl { font-size:8px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; margin-bottom:3px; }
    .stat-val { font-family:var(--head); font-size:17px; font-weight:800; line-height:1; }
    .stat-sub { font-size:8px; color:var(--muted); margin-top:2px; }

    /* ── Signals — fill full width ── */
    .signals-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:8px; }
    @media(max-width:500px){ .signals-grid{ grid-template-columns:1fr; } }
    .sig-card { background:var(--bg2); border:1px solid var(--border); border-radius:7px; padding:10px; cursor:pointer; transition:.1s; }
    .sig-card:hover { border-color:var(--border2); background:var(--bg3); }
    .sig-card.exp { border-color:rgba(56,189,248,.3); }
    .score-ring { width:34px; height:34px; border-radius:50%; border:2px solid; display:flex; align-items:center; justify-content:center; font-size:10px; font-weight:700; flex-shrink:0; }
    .badge { display:inline-block; padding:1px 5px; border-radius:3px; font-size:8px; font-weight:700; text-transform:uppercase; }
    .order-box { background:var(--bg3); border-radius:4px; padding:6px 8px; margin:5px 0; font-size:9px; }
    .order-leg { display:flex; justify-content:space-between; padding:2px 0; font-size:9px; }

    /* ── Chart ── */
    .chart-card { background:var(--bg2); border:1px solid var(--border); border-radius:7px; margin-bottom:11px; overflow:hidden; }
    .chart-head { display:flex; align-items:center; justify-content:space-between; padding:8px 12px; border-bottom:1px solid var(--border); }
    .ctab { padding:2px 6px; border-radius:4px; font-size:9px; font-weight:700; cursor:pointer; color:var(--muted); border:1px solid transparent; }
    .ctab.active { background:var(--bg3); color:var(--accent); border-color:var(--border2); }

    /* ── Prev day ── */
    .prev-grid { display:grid; grid-template-columns:repeat(6,1fr); gap:5px; padding:8px 12px; border-top:1px solid var(--border); }
    @media(max-width:700px){ .prev-grid{ grid-template-columns:repeat(3,1fr); } }
    .prev-cell { background:var(--bg3); border-radius:4px; padding:5px 7px; }
    .prev-lbl { font-size:8px; color:var(--muted); text-transform:uppercase; margin-bottom:2px; }
    .prev-val { font-size:10px; font-weight:700; }

    /* ── Mood ── */
    .mood-bar { height:6px; border-radius:4px; overflow:hidden; background:var(--bg3); display:flex; }

    /* ── Regime ── */
    .regime-banner { border-radius:6px; padding:9px 12px; margin-bottom:12px; border-left:3px solid; }

    /* ── Empty ── */
    .empty { text-align:center; padding:40px; color:var(--muted); font-size:11px; }
    .empty-icon { font-size:26px; margin-bottom:8px; }

    /* ── Auth ── */
    .auth-wrap { min-height:100%; display:flex; align-items:center; justify-content:center; background:var(--bg); position:relative; overflow:auto; padding:20px; }
    .auth-grid { position:fixed; inset:0; background-image:linear-gradient(rgba(56,189,248,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,.025) 1px,transparent 1px); background-size:55px 55px; pointer-events:none; }
    .auth-card { width:min(380px,100%); background:var(--bg2); border:1px solid var(--border); border-radius:10px; padding:24px; position:relative; z-index:1; }
    .auth-input { width:100%; background:var(--bg3); border:1px solid var(--border2); color:var(--text); padding:8px 10px; border-radius:5px; font-family:var(--mono); font-size:11px; outline:none; }
    .auth-input:focus { border-color:var(--accent); }
    .auth-btn { width:100%; background:var(--accent); color:#000; border:none; border-radius:5px; padding:9px; font-family:var(--mono); font-size:11px; font-weight:700; cursor:pointer; }

    /* ── Margin ── */
    .margin-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    @media(max-width:700px){ .margin-grid{ grid-template-columns:1fr; } }
    .preset-btn { padding:5px 12px; border-radius:20px; border:1px solid var(--border2); background:transparent; color:var(--muted); cursor:pointer; font-family:var(--mono); font-size:10px; font-weight:700; transition:.1s; }
    .preset-btn.active { border-color:var(--accent); color:var(--accent); background:rgba(56,189,248,.1); }

    /* ── Setup screen ── */
    .setup-wrap { min-height:100%; display:flex; align-items:center; justify-content:center; background:var(--bg); overflow:auto; position:relative; padding:20px; }
    .setup-grid { position:fixed; inset:0; background-image:linear-gradient(rgba(56,189,248,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,.025) 1px,transparent 1px); background-size:55px 55px; pointer-events:none; }
    .setup-card { width:min(500px,100%); background:var(--bg2); border:1px solid var(--border); border-radius:12px; overflow:hidden; position:relative; z-index:1; }
    .step-tab { flex:1; padding:9px; text-align:center; font-size:9px; font-weight:700; border-bottom:2px solid transparent; transition:.2s; }
    .step-tab.done { color:var(--accent); border-bottom-color:var(--accent); }

    /* ── Plans ── */
    .plans-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:11px; }
    .plan-card { background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:16px; }
    .plan-card.featured { border-color:var(--yellow); }

    /* ── Paper ── */
    .paper-stats { display:grid; grid-template-columns:repeat(3,1fr); gap:9px; margin-bottom:14px; }
    @media(max-width:600px){ .paper-stats{ grid-template-columns:1fr 1fr; } }
  `}</style>
);

// ════════════════════════════════════════════════════════════════
//  HOOKS
// ════════════════════════════════════════════════════════════════
const useApi = token => {
  const get  = useCallback(p => fetch(`${API}${p}`, { headers:{ Authorization:`Bearer ${token}` }}).then(r=>r.json()), [token]);
  const post = useCallback((p,b) => fetch(`${API}${p}`, { method:"POST", headers:{ "Content-Type":"application/json", Authorization:`Bearer ${token}` }, body:JSON.stringify(b) }).then(r=>r.json()), [token]);
  return { get, post };
};

function useLivePrices() {
  const [p, setP] = useState({ nifty:23874, bank:51240, btc:93240, vix:16.42 });
  useEffect(() => {
    const iv = setInterval(() => setP(prev => ({
      nifty: Math.round(prev.nifty + (Math.random()-.45)*25),
      bank:  Math.round(prev.bank  + (Math.random()-.45)*60),
      btc:   Math.round(prev.btc   + (Math.random()-.45)*120),
      vix:   Math.max(10, Math.min(35, +(prev.vix + (Math.random()-.5)*.08).toFixed(2))),
    })), 3000);
    return () => clearInterval(iv);
  }, []);
  return p;
}

function useCandles(market) {
  const [data, setData] = useState([]);
  useEffect(() => {
    const base = market==="CRYPTO"?93000:market==="BANKNIFTY_FO"?51200:23850;
    let price = base;
    setData(Array.from({length:50}, (_,i) => {
      const o=price, mv=(Math.random()-.45)*(base*.003);
      const c=o+mv, h=Math.max(o,c)+(Math.random()*base*.001), l=Math.min(o,c)-(Math.random()*base*.001);
      price=c;
      return { i, open:+o.toFixed(0), close:+c.toFixed(0), high:+h.toFixed(0), low:+l.toFixed(0), vol:Math.round(Math.random()*4e6+1e6) };
    }));
  }, [market]);
  return data;
}

// ════════════════════════════════════════════════════════════════
//  SIGNAL GENERATOR (mock — replaced by WebSocket in prod)
// ════════════════════════════════════════════════════════════════
const SIG_TEMPLATES = {
  EQUITY_INDIA: [
    { strat:"E1 EMA CROSSOVER",  risk:"MED",  detail:"EMA9 crossed above EMA21 · Vol confirmed" },
    { strat:"E2 VWAP REVERSION", risk:"MED",  detail:"Price 0.48% below VWAP · Bounce expected" },
    { strat:"E3 ORB BREAKOUT",   risk:"MED",  detail:"Broke 15-min range high · Vol surge" },
    { strat:"E4 TREND FOLLOW",   risk:"MED",  detail:"ADX 28.4 strong trend · Pull to EMA21" },
    { strat:"E5 FUNDING ARB",    risk:"LOW",  detail:"Funding +0.024% · Longs paying — go short" },
    { strat:"E7 S/R LEVEL",      risk:"LOW",  detail:"Price at 24h low with OI support wall" },
  ],
  NIFTY_FO: [
    { strat:"S1 CALENDAR SPREAD", risk:"LOW",  detail:"Dev -3.2pts vs Fair · Theta edge 0.018" },
    { strat:"S2 IRON CONDOR",     risk:"LOW",  detail:"Net credit 18.4pts · BE [23400–24300]" },
    { strat:"S3 SHORT STRADDLE",  risk:"MED",  detail:"VIX 16.4 elevated · Total premium 286pts" },
    { strat:"S6 EXPIRY 0DTE",     risk:"HIGH", detail:"DTE=1 · Max theta decay · Exit by 3:20 PM" },
  ],
  BANKNIFTY_FO: [
    { strat:"S1 CALENDAR SPREAD", risk:"LOW",  detail:"BankNifty near/far dev -4.1pts" },
    { strat:"S2 IRON CONDOR",     risk:"LOW",  detail:"Net credit 42pts · Range [50800–51600]" },
    { strat:"S3 SHORT STRADDLE",  risk:"MED",  detail:"IV 18.6% · Straddle 820pts" },
    { strat:"S6 EXPIRY 0DTE",     risk:"HIGH", detail:"Weekly expiry · Exit by 3:20 PM" },
  ],
  CRYPTO: [
    { strat:"BTC MOMENTUM",   risk:"MED",  detail:"1.4% uptrend · Vol 12B · Funding +0.012%" },
    { strat:"ETH FUNDING ARB",risk:"LOW",  detail:"Funding -0.032% · Shorts paying — go long" },
    { strat:"SOL MOMENTUM",   risk:"MED",  detail:"2.1% breakout · Vol surge 3.2x avg" },
    { strat:"BTC SCALP",      risk:"HIGH", detail:"Orderbook imbalance 3:1 bid side" },
  ],
};

function makeSig(market) {
  const tpls = SIG_TEMPLATES[market] || SIG_TEMPLATES.NIFTY_FO;
  const t    = tpls[Math.floor(Math.random()*tpls.length)];
  const score= Math.floor(Math.random()*45+50);
  const dir  = Math.random()>.4?"LONG":"SHORT";
  const isCrypto = market==="CRYPTO";
  const base = isCrypto?93240:market==="BANKNIFTY_FO"?51240:23874;
  const entry = isCrypto?+(base*(1+(Math.random()-.5)*.002)).toFixed(1):+(Math.random()*5-2.5).toFixed(2);
  return {
    id:       Math.random().toString(36).slice(2,8).toUpperCase(),
    market,
    strategy: t.strat,
    risk:     t.risk,
    detail:   t.detail,
    score, direction:dir,
    entry_at: entry,
    target:   isCrypto?+(base*1.018).toFixed(0):(+entry+(dir==="LONG"?4:-4)).toFixed(2),
    sl:       isCrypto?+(base*.985).toFixed(0):(+entry+(dir==="LONG"?-3:3)).toFixed(2),
    lots:     Math.floor(Math.random()*8)+1,
    near_bid: isCrypto?+(base*.9996).toFixed(0):+(142+Math.random()*20).toFixed(2),
    near_ask: isCrypto?+(base*1.0004).toFixed(0):+(143+Math.random()*20).toFixed(2),
    far_bid:  isCrypto?null:+(152+Math.random()*20).toFixed(2),
    far_ask:  isCrypto?null:+(153+Math.random()*20).toFixed(2),
    time:     new Date().toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",second:"2-digit"}),
    expanded: false,
    nse_enhanced: true,
    pcr:      (Math.random()*.8+.7).toFixed(2),
    support:  Math.round(23700+Math.random()*200),
    resistance: Math.round(24100+Math.random()*200),
  };
}

// ════════════════════════════════════════════════════════════════
//  CANDLE CHART SVG
// ════════════════════════════════════════════════════════════════
const CandleSVG = ({ candles }) => {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current || !candles.length) return;
    const svg = ref.current;
    const W = svg.clientWidth || 700, H = 190;
    const prices = candles.flatMap(d=>[d.high,d.low]);
    const mn = Math.min(...prices), mx = Math.max(...prices);
    const pad = 16, cw = (W-pad)/candles.length, bw = cw*.6;
    const sy = v => pad + ((mx-v)/(mx-mn||1))*(H-2*pad);
    svg.setAttribute("viewBox",`0 0 ${W} ${H}`);
    svg.innerHTML = candles.map(d=>{
      const x=pad+d.i*cw+cw/2, up=d.close>=d.open, col=up?"#34d399":"#f87171";
      const y1=Math.min(sy(d.open),sy(d.close)), h=Math.max(Math.abs(sy(d.open)-sy(d.close)),1);
      return `<line x1="${x}" y1="${sy(d.high)}" x2="${x}" y2="${sy(d.low)}" stroke="${col}" stroke-width=".8" opacity=".5"/>
        <rect x="${(x-bw/2).toFixed(1)}" y="${y1.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${col}" rx="1"/>`;
    }).join("")
    + `<text x="${pad}" y="${H-3}" fill="rgba(255,255,255,.2)" font-size="8">09:15</text>`
    + `<text x="${(W/2).toFixed(0)}" y="${H-3}" fill="rgba(255,255,255,.2)" font-size="8">12:15</text>`
    + `<text x="${W-36}" y="${H-3}" fill="rgba(255,255,255,.2)" font-size="8">15:30</text>`;
  }, [candles]);
  return <svg ref={ref} style={{ width:"100%", height:190 }} />;
};

// ════════════════════════════════════════════════════════════════
//  SIGNAL CARD
// ════════════════════════════════════════════════════════════════
const SignalCard = ({ sig, onToggle, margin }) => {
  const rc    = RISK_C[sig.risk] || RISK_C.MED;
  const sc    = sig.score;
  const scCol = sc>=80?"#34d399":sc>=65?"#fbbf24":"#fb923c";
  const isCrypto = sig.market==="CRYPTO";
  const marginPerLot = isCrypto?5000:80000;
  const stratCap     = sig.risk==="LOW"?.40:sig.risk==="MED"?.25:.15;
  const smult        = sc>=85?1:sc>=75?.8:sc>=65?.6:sc>=55?.4:.2;
  const dynLots      = Math.max(1, Math.round(Math.floor(margin*stratCap/marginPerLot)*smult));
  const tp_inr       = Math.round(4*(isCrypto?1:25)*dynLots);
  const sl_inr       = Math.round(3*(isCrypto?1:25)*dynLots);
  const mktColor     = { EQUITY_INDIA:"#4ade80", NIFTY_FO:"#34d399", BANKNIFTY_FO:"#fbbf24", CRYPTO:"#fb923c" }[sig.market]||"#38bdf8";

  return (
    <div className={`sig-card${sig.expanded?" exp":""}`} onClick={()=>onToggle(sig.id)}>
      <div style={{display:"flex",alignItems:"center",gap:10}}>
        <div className="score-ring" style={{borderColor:scCol,color:scCol}}>{sc}</div>
        <div style={{flex:1,minWidth:0}}>
          <div style={{display:"flex",alignItems:"center",gap:5,marginBottom:3,flexWrap:"wrap"}}>
            <span style={{fontWeight:700,fontSize:11}}>{sig.strategy}</span>
            <span className="badge" style={{background:rc.bg,color:rc.color,border:`1px solid ${rc.border}`}}>{sig.risk}</span>
            {sig.nse_enhanced && <span className="badge" style={{background:"rgba(56,189,248,.1)",color:"#38bdf8",border:"1px solid rgba(56,189,248,.25)"}}>NSE</span>}
          </div>
          <div style={{fontSize:10,color:"var(--muted)",lineHeight:1.4}}>{sig.detail}</div>
          <div style={{marginTop:3,fontSize:10}}>
            <span style={{color:sig.direction==="LONG"?"var(--green)":"var(--red)",fontWeight:700}}>
              {sig.direction==="LONG"?"▲":"▼"} {sig.direction}
            </span>
            <span style={{color:"var(--dim)",marginLeft:8}}>{sig.time}</span>
          </div>
        </div>
        <div style={{textAlign:"right",flexShrink:0}}>
          <div style={{fontFamily:"var(--head)",fontSize:13,fontWeight:800,color:scCol}}>{sc}</div>
          <div style={{fontSize:9,color:"var(--dim)"}}>/100</div>
        </div>
      </div>

      {sig.expanded && (
        <div style={{paddingTop:10,borderTop:"1px solid var(--border)",marginTop:8}}>
          {/* NSE context if available */}
          {sig.nse_enhanced && (
            <div style={{display:"flex",gap:10,marginBottom:8,fontSize:9,color:"var(--muted)",background:"rgba(56,189,248,.05)",borderRadius:4,padding:"5px 8px"}}>
              <span>PCR: <strong style={{color:"var(--accent)"}}>{sig.pcr}</strong></span>
              <span>Support: <strong style={{color:"var(--green)"}}>{sig.support}</strong></span>
              <span>Resistance: <strong style={{color:"var(--red)"}}>{sig.resistance}</strong></span>
            </div>
          )}

          <div className="order-box">
            <div style={{fontSize:8,color:"var(--muted)",fontWeight:700,marginBottom:5,textTransform:"uppercase",letterSpacing:".08em"}}>
              Order Instructions — {dynLots} lot{dynLots!==1?"s":""} · ₹{(dynLots*marginPerLot).toLocaleString("en-IN")} margin
            </div>
            {!isCrypto ? (<>
              <div className="order-leg">
                <span style={{color:sig.direction==="LONG"?"var(--green)":"var(--red)",fontWeight:700}}>
                  LEG 1 · {sig.direction==="LONG"?"BUY  Far":"SELL Far"} {sig.strategy.includes("STRADDLE")?"CE+PE":"CE"}
                </span>
                <span>@ {sig.direction==="LONG"?sig.far_ask:sig.far_bid}</span>
              </div>
              <div className="order-leg">
                <span style={{color:sig.direction==="LONG"?"var(--red)":"var(--green)",fontWeight:700}}>
                  LEG 2 · {sig.direction==="LONG"?"SELL Near":"BUY  Near"} {sig.strategy.includes("STRADDLE")?"CE+PE":"CE"}
                </span>
                <span>@ {sig.direction==="LONG"?sig.near_bid:sig.near_ask}</span>
              </div>
            </>) : (
              <div className="order-leg">
                <span style={{color:sig.direction==="LONG"?"var(--green)":"var(--red)",fontWeight:700}}>
                  {sig.direction==="LONG"?"BUY ":"SELL"} {sig.strategy.split(" ")[0]} LIMIT
                </span>
                <span>@ {sig.entry_at}</span>
              </div>
            )}
            <div style={{display:"flex",gap:10,fontSize:9,marginTop:6}}>
              <span style={{color:"var(--green)"}}>TARGET: {sig.target} (+₹{tp_inr.toLocaleString("en-IN")})</span>
              <span style={{color:"var(--red)"}}>SL: {sig.sl} (-₹{sl_inr.toLocaleString("en-IN")})</span>
              <span style={{color:"var(--accent)"}}>R:R 1.3:1</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// ════════════════════════════════════════════════════════════════
//  MARKET PAGE — one dedicated page per market
// ════════════════════════════════════════════════════════════════
const PREV_DAY_DATA = {
  EQUITY_INDIA:   [{l:"Prev Close",v:"23,621",pos:false},{l:"Open",v:"23,680"},{l:"High",v:"23,942",pos:true},{l:"Low",v:"23,580",neg:true},{l:"Change",v:"+1.07%",pos:true},{l:"Volume",v:"12.4Cr"}],
  NIFTY_FO:       [{l:"ATM Strike",v:"23,900"},{l:"Straddle",v:"286 pts"},{l:"PCR",v:"0.87"},{l:"Max Pain",v:"23,800"},{l:"VIX",v:"16.42"},{l:"DTE",v:"12"}],
  BANKNIFTY_FO:   [{l:"ATM Strike",v:"51,200"},{l:"Straddle",v:"820 pts"},{l:"PCR",v:"0.91"},{l:"Max Pain",v:"51,000"},{l:"IV",v:"18.6%"},{l:"DTE",v:"12"}],
  CRYPTO:         [{l:"BTC Prev",v:"$91,200"},{l:"BTC High",v:"$94,100"},{l:"ETH",v:"$3,180"},{l:"SOL",v:"$182"},{l:"BTC Chg",v:"+2.24%",pos:true},{l:"Funding",v:"+0.012%"}],
};

const REGIME_DATA = {
  EQUITY_INDIA:   {name:"R2 SIDEWAYS LOW",  color:"#34d399", advice:"EMA crossover + ORB signals active. NSE OI showing support at 23,700."},
  NIFTY_FO:       {name:"R2 SIDEWAYS LOW",  color:"#34d399", advice:"Ideal for Calendar Spread + Iron Condor. PCR 0.87 neutral. Full margin deployment."},
  BANKNIFTY_FO:   {name:"R3 ELEVATED IV",   color:"#fbbf24", advice:"Sell premium — Short Straddle + IC. IV 18.6% above average. Use wings to cap risk."},
  CRYPTO:         {name:"TRENDING BULL",    color:"#fb923c", advice:"BTC momentum positive. Funding +0.012% — longs paying. Monitor for exhaustion."},
};

const MarketPage = ({ market, activeStrat, signals, onToggle, prices, margin }) => {
  const [chartMode, setChartMode] = useState("candle");
  const candles   = useCandles(market);
  const mktDef    = SIDEBAR_NAV.find(n=>n.id===market);
  const regime    = REGIME_DATA[market] || REGIME_DATA.EQUITY_INDIA;
  const prevDay   = PREV_DAY_DATA[market] || PREV_DAY_DATA.NIFTY_FO;
  const mktColor  = mktDef?.color || "#38bdf8";

  // Signals are already market-isolated (signals[activeMkt] passed as prop)
  // Only need to filter by strategy subcategory within this market
  const filtered = useMemo(() => {
    if (!activeStrat || activeStrat === "ALL") return signals;
    const stratDef = mktDef?.strategies.find(s => s.id === activeStrat);
    if (!stratDef) return signals;
    const tag = stratDef.tag.toLowerCase();
    return signals.filter(s => s.strategy.toLowerCase().includes(tag));
  }, [signals, activeStrat, mktDef]);

  const longs  = filtered.filter(s=>s.direction==="LONG").length;
  const shorts  = filtered.filter(s=>s.direction==="SHORT").length;
  const total  = longs + shorts || 1;
  const longPct = Math.round(longs/total*100);

  // Stats
  const stats = useMemo(()=>{
    if (market==="CRYPTO") return [
      {l:"BTC",     v:`$${prices.btc.toLocaleString("en-US")}`, s:"+1.4% 24h",   c:"var(--orange)"},
      {l:"Signal Count",v:filtered.length,                      s:"Active now",   c:"var(--accent)"},
      {l:"Top Score",   v:filtered.length?Math.max(...filtered.map(s=>s.score)):0,s:"Best signal",  c:"var(--green)"},
      {l:"Mood",        v:longPct>=60?"BULLISH":longPct<=40?"BEARISH":"NEUTRAL",  s:`${longPct}% long`, c:longPct>=60?"var(--green)":longPct<=40?"var(--red)":"var(--yellow)"},
    ];
    if (market==="BANKNIFTY_FO") return [
      {l:"BankNifty",   v:prices.bank.toLocaleString("en-IN"),  s:"Live spot",   c:"var(--yellow)"},
      {l:"Signal Count",v:filtered.length,                      s:"Active now",  c:"var(--accent)"},
      {l:"Top Score",   v:filtered.length?Math.max(...filtered.map(s=>s.score)):0,s:"Best signal",c:"var(--green)"},
      {l:"Mood",        v:longPct>=60?"BULLISH":longPct<=40?"BEARISH":"NEUTRAL",  s:`${longPct}% long`,c:longPct>=60?"var(--green)":longPct<=40?"var(--red)":"var(--yellow)"},
    ];
    return [
      {l:"NIFTY Spot",  v:prices.nifty.toLocaleString("en-IN"),s:"NSE Live",    c:"var(--green)"},
      {l:"VIX",         v:prices.vix,                          s:prices.vix<16?"LOW":prices.vix<19?"MED":"HIGH", c:prices.vix<16?"var(--green)":prices.vix<19?"var(--yellow)":"var(--orange)"},
      {l:"Signal Count",v:filtered.length,                     s:"Active now",  c:"var(--accent)"},
      {l:"Top Score",   v:filtered.length?Math.max(...filtered.map(s=>s.score)):0,s:"Best signal",c:"var(--green)"},
    ];
  }, [market, prices, filtered, longPct]);

  return (
    <div className="page">
      <div className="page-inner">
        {/* Page header */}
        <div className="page-head">
          <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:4}}>
            <div style={{width:10,height:10,borderRadius:"50%",background:mktColor}}/>
            <div className="page-title">{mktDef?.label}</div>
            <div style={{fontSize:9,color:"var(--muted)",background:"var(--bg3)",border:"1px solid var(--border2)",borderRadius:4,padding:"2px 8px"}}>{mktDef?.source}</div>
          </div>
          <div className="page-sub">{mktDef?.desc}</div>
        </div>

        {/* Regime banner */}
        <div className="regime-banner" style={{background:regime.color+"10",borderLeftColor:regime.color,marginBottom:14}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:3}}>
            <div className="live-dot"/>
            <span style={{fontFamily:"var(--head)",fontWeight:800,color:regime.color,fontSize:13}}>{regime.name}</span>
            <span style={{fontSize:9,color:"var(--muted)",marginLeft:"auto"}}>{mktDef?.source}</span>
          </div>
          <div style={{fontSize:10,color:"var(--muted)",lineHeight:1.5}}>{regime.advice}</div>
        </div>

        {/* Stats */}
        <div className="stats-row">
          {stats.map((s,i)=>(
            <div key={i} className="stat-card">
              <div className="stat-lbl">{s.l}</div>
              <div className="stat-val" style={{color:s.c,fontSize:18}}>{s.v}</div>
              <div className="stat-sub">{s.s}</div>
            </div>
          ))}
        </div>

        {/* Chart */}
        <div className="chart-card">
          <div className="chart-head">
            <div>
              <div style={{fontFamily:"var(--head)",fontWeight:800,fontSize:13}}>{mktDef?.label} — Intraday</div>
              <div style={{fontSize:9,color:"var(--muted)"}}>{mktDef?.source}</div>
            </div>
            <div style={{display:"flex",gap:4}}>
              {["candle","line","volume"].map(m=>(
                <div key={m} className={`ctab${chartMode===m?" active":""}`} onClick={()=>setChartMode(m)} style={{textTransform:"capitalize"}}>{m}</div>
              ))}
            </div>
          </div>
          <div style={{padding:"8px 10px 0"}}>
            {chartMode==="candle" && <CandleSVG candles={candles}/>}
            {chartMode==="line" && (
              <ResponsiveContainer width="100%" height={190}>
                <AreaChart data={candles.map(d=>({t:d.i,v:d.close}))}>
                  <defs><linearGradient id="lg" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={mktColor} stopOpacity={.3}/><stop offset="95%" stopColor={mktColor} stopOpacity={0}/></linearGradient></defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,.04)"/>
                  <XAxis dataKey="t" hide/><YAxis tick={{fill:"#4a6080",fontSize:8}} tickLine={false} axisLine={false} width={50} tickFormatter={v=>v>1000?`${(v/1000).toFixed(0)}k`:v}/>
                  <Tooltip contentStyle={{background:"#0f1729",border:"1px solid rgba(255,255,255,.1)",borderRadius:5,fontSize:10}}/>
                  <Area type="monotone" dataKey="v" stroke={mktColor} strokeWidth={1.5} fill="url(#lg)"/>
                </AreaChart>
              </ResponsiveContainer>
            )}
            {chartMode==="volume" && (
              <ResponsiveContainer width="100%" height={190}>
                <BarChart data={candles}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,.04)"/>
                  <XAxis dataKey="i" hide/><YAxis tick={{fill:"#4a6080",fontSize:8}} tickLine={false} axisLine={false} width={45} tickFormatter={v=>`${(v/1e6).toFixed(0)}M`}/>
                  <Tooltip contentStyle={{background:"#0f1729",border:"1px solid rgba(255,255,255,.1)",borderRadius:5,fontSize:10}}/>
                  <Bar dataKey="vol" radius={[2,2,0,0]}>{candles.map((c,i)=><Cell key={i} fill={c.close>=c.open?"#34d399":"#f87171"} opacity={.7}/>)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="prev-grid">
            {prevDay.map((c,i)=>(
              <div key={i} className="prev-cell">
                <div className="prev-lbl">{c.l}</div>
                <div className="prev-val" style={{color:c.pos?"var(--green)":c.neg?"var(--red)":"var(--text)"}}>{c.v}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Market mood */}
        <div className="card card-pad" style={{marginBottom:14}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:5}}>
            <span style={{fontSize:9,fontWeight:700,textTransform:"uppercase",letterSpacing:".09em",color:"var(--muted)"}}>Signal Sentiment</span>
            <span style={{fontSize:10,fontWeight:700,color:longPct>=60?"var(--green)":longPct<=40?"var(--red)":"var(--yellow)"}}>
              {longPct>=60?"BULLISH":longPct<=40?"BEARISH":"NEUTRAL"} ({filtered.length} signals)
            </span>
          </div>
          <div className="mood-bar">
            <div style={{width:`${longPct}%`,height:"100%",background:"var(--green)",transition:"width .5s"}}/>
            <div style={{width:`${100-longPct}%`,height:"100%",background:"var(--red)",transition:"width .5s"}}/>
          </div>
          <div style={{display:"flex",justifyContent:"space-between",fontSize:9,color:"var(--muted)",marginTop:3}}>
            <span>{longs} Long signals ({longPct}%)</span>
            <span>{shorts} Short signals ({100-longPct}%)</span>
          </div>
        </div>

        {/* Signals grid */}
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:10}}>
          <span style={{fontFamily:"var(--head)",fontWeight:800,fontSize:14}}>
            Live Signals
            {activeStrat && activeStrat!=="ALL" && (
              <span style={{fontSize:10,color:"var(--accent)",marginLeft:8,fontFamily:"var(--mono)"}}>
                · {mktDef?.strategies.find(s=>s.id===activeStrat)?.label}
              </span>
            )}
          </span>
          <span style={{background:"var(--accent)",color:"#000",borderRadius:20,padding:"1px 8px",fontSize:9,fontWeight:700}}>{filtered.length}</span>
        </div>

        {filtered.length===0 ? (
          <div className="empty">
            <div className="empty-icon">📡</div>
            Waiting for signals from {mktDef?.source}...
          </div>
        ) : (
          <div className="signals-grid">
            {filtered.slice(0,20).map(s=>(
              <SignalCard key={s.id} sig={s} onToggle={onToggle} margin={margin}/>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ════════════════════════════════════════════════════════════════
//  MARGIN PAGE
// ════════════════════════════════════════════════════════════════
const MarginPage = ({ margin, onUpdate }) => {
  const [raw, setRaw]     = useState("");
  const [parsed, setParsed] = useState(null);
  const [saved, setSaved]   = useState(false);

  const parse = v => {
    const s = v.replace(/,/g,"").trim().toUpperCase();
    if(!s) return null;
    if(s.endsWith("CR")||s.endsWith("C")) return parseFloat(s)*10_000_000;
    if(s.endsWith("L")) return parseFloat(s)*100_000;
    const n=parseFloat(s); return isNaN(n)?null:n;
  };

  const apply = () => {
    if (!parsed||parsed<=0) return;
    onUpdate(parsed);
    setSaved(true);
    setTimeout(()=>setSaved(false),2500);
    setRaw(""); setParsed(null);
  };

  const INST = [
    {n:"NIFTY",      m:80000, c:"#34d399"}, {n:"BANKNIFTY",m:90000, c:"#fbbf24"},
    {n:"FINNIFTY",   m:50000, c:"#a78bfa"}, {n:"BTCUSD",   m:5000,  c:"#fb923c"},
  ];
  const STRATS = [
    {n:"Calendar Spread",cap:.40,m:80000},{n:"Iron Condor",cap:.40,m:80000},
    {n:"Short Straddle", cap:.20,m:80000},{n:"Expiry 0DTE",cap:.15,m:80000},
    {n:"BTC Momentum",   cap:.25,m:5000}, {n:"Funding Arb",cap:.25,m:5000},
  ];

  return (
    <div className="page">
      <div className="page-inner">
        <div className="page-head">
          <div className="page-title">Margin Management</div>
          <div className="page-sub">Session margin controls lot sizing across all strategies. Changes take effect on the next signal cycle.</div>
        </div>

        {/* Current margin highlight */}
        <div style={{background:"rgba(56,189,248,.07)",border:"1px solid rgba(56,189,248,.2)",borderRadius:8,padding:"14px 18px",marginBottom:18,display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div>
            <div style={{fontSize:9,color:"var(--muted)",textTransform:"uppercase",letterSpacing:".09em",marginBottom:3}}>Current Session Margin</div>
            <div style={{fontFamily:"var(--head)",fontSize:28,fontWeight:800,color:"var(--accent)"}}>{fmtMargin(margin)}</div>
          </div>
          <div style={{textAlign:"right",fontSize:10,color:"var(--muted)"}}>
            <div>Max NIFTY lots: <strong style={{color:"var(--text)"}}>{Math.floor(margin/80000)}</strong></div>
            <div>Max BTC contracts: <strong style={{color:"var(--text)"}}>{Math.floor(margin/5000)}</strong></div>
          </div>
        </div>

        <div className="margin-grid">
          {/* Update section */}
          <div>
            <div className="card card-pad" style={{marginBottom:12}}>
              <div style={{fontFamily:"var(--head)",fontWeight:700,fontSize:14,marginBottom:12}}>Update Margin</div>
              <div style={{fontSize:9,color:"var(--muted)",textTransform:"uppercase",letterSpacing:".09em",marginBottom:7}}>Quick Presets</div>
              <div style={{display:"flex",gap:7,flexWrap:"wrap",marginBottom:14}}>
                {PRESETS.map(p=>(
                  <button key={p.label} className={`preset-btn${parsed===p.value?" active":""}`} onClick={()=>{setParsed(p.value);setRaw("");}}>
                    {p.label}
                  </button>
                ))}
              </div>
              <div style={{fontSize:9,color:"var(--muted)",textTransform:"uppercase",letterSpacing:".09em",marginBottom:6}}>Custom Amount</div>
              <div style={{position:"relative",marginBottom:10}}>
                <span style={{position:"absolute",left:10,top:"50%",transform:"translateY(-50%)",color:"var(--muted)",fontSize:14}}>₹</span>
                <input className="auth-input" value={raw} onChange={e=>{setRaw(e.target.value);setParsed(parse(e.target.value));}}
                  onKeyDown={e=>e.key==="Enter"&&apply()} placeholder="25L or 2500000" style={{paddingLeft:26}}/>
              </div>
              {parsed && <div style={{fontSize:10,color:"var(--green)",marginBottom:10}}>✓ {fmtMargin(parsed)}</div>}
              <button onClick={apply} disabled={!parsed||parsed<=0}
                style={{width:"100%",background:saved?"var(--green)":"var(--accent)",color:"#000",border:"none",borderRadius:5,padding:"9px",fontFamily:"var(--mono)",fontSize:11,fontWeight:700,cursor:parsed?"pointer":"not-allowed",opacity:parsed?1:.5,transition:".2s"}}>
                {saved?"✓ Margin Updated!":"Apply New Margin"}
              </button>
              <div style={{marginTop:10,fontSize:9,color:"var(--dim)",lineHeight:1.5,padding:"8px 10px",background:"var(--bg3)",borderRadius:5}}>
                Lot sizing = Score × VIX × Regime × Strategy cap × Win streak.<br/>
                Range: 1 lot minimum → margin-constrained maximum.
              </div>
            </div>
          </div>

          {/* Allocation breakdown */}
          <div>
            <div className="card card-pad" style={{marginBottom:10}}>
              <div style={{fontFamily:"var(--head)",fontWeight:700,fontSize:13,marginBottom:12}}>Max Lots by Instrument</div>
              {INST.map((im,i)=>{
                const ml=Math.floor(margin/im.m);
                return (
                  <div key={i} style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"7px 0",borderBottom:i<INST.length-1?"1px solid var(--border)":"none"}}>
                    <div style={{display:"flex",alignItems:"center",gap:7}}>
                      <div style={{width:7,height:7,borderRadius:"50%",background:im.c}}/>
                      <span style={{fontWeight:700,color:im.c}}>{im.n}</span>
                    </div>
                    <div style={{textAlign:"right"}}>
                      <span style={{fontFamily:"var(--head)",fontWeight:800,fontSize:14,color:ml>0?"var(--text)":"var(--red)"}}>{ml>0?ml+" lots":"—"}</span>
                      <div style={{fontSize:8,color:"var(--dim)"}}>₹{im.m.toLocaleString("en-IN")}/lot</div>
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="card card-pad">
              <div style={{fontFamily:"var(--head)",fontWeight:700,fontSize:13,marginBottom:12}}>Max Lots by Strategy</div>
              {STRATS.map((s,i)=>{
                const ml=Math.max(1,Math.floor(margin*s.cap/s.m));
                return (
                  <div key={i} style={{marginBottom:9}}>
                    <div style={{display:"flex",justifyContent:"space-between",marginBottom:3}}>
                      <span style={{fontSize:10,fontWeight:700}}>{s.n}</span>
                      <span style={{fontFamily:"var(--head)",fontWeight:800,fontSize:12,color:ml>=5?"var(--green)":ml>=2?"var(--yellow)":"var(--orange)"}}>{ml} lots</span>
                    </div>
                    <div style={{height:4,background:"var(--bg3)",borderRadius:2,overflow:"hidden"}}>
                      <div style={{height:"100%",width:`${s.cap*100}%`,background:"var(--accent)",opacity:.5,borderRadius:2}}/>
                    </div>
                    <div style={{fontSize:8,color:"var(--dim)",marginTop:2}}>{Math.round(s.cap*100)}% cap · ₹{s.m.toLocaleString("en-IN")}/lot</div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ════════════════════════════════════════════════════════════════
//  ANALYTICS PAGE (placeholder)
// ════════════════════════════════════════════════════════════════
const AnalyticsPage = ({ token }) => (
  <div className="page"><div className="page-inner">
    <div className="page-head">
      <div className="page-title">Analytics</div>
      <div className="page-sub">Trade history, win rate, and strategy performance</div>
    </div>
    <div className="empty" style={{padding:60}}>
      <div className="empty-icon">📊</div>
      Log trades via Ctrl+C in your algo scripts to see analytics here.
    </div>
  </div></div>
);

// ════════════════════════════════════════════════════════════════
//  PAPER TRADE PAGE
// ════════════════════════════════════════════════════════════════
const PaperPage = ({ margin }) => {
  const [trades, setTrades] = useState([]);
  const [pnl, setPnl]       = useState(0);
  const balance = margin - trades.reduce((a,t)=>a+(t.margin_used||0),0);

  const addTrade = () => {
    const mkt  = ["NIFTY_FO","BANKNIFTY_FO","EQUITY_INDIA","CRYPTO"][Math.floor(Math.random()*4)];
    const sig  = makeSig(mkt);
    const used = mkt==="CRYPTO"?5000:80000;
    const t    = { id:sig.id, strategy:sig.strategy, market:mkt, direction:sig.direction,
      entry:sig.entry_at, target:sig.target, sl:sig.sl,
      lots:Math.floor(Math.random()*4)+1, margin_used:used, status:"OPEN",
      time:new Date().toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit"}) };
    setTrades(prev=>[t,...prev].slice(0,20));
  };

  const closeTrade = (id) => {
    setTrades(prev => prev.map(t => {
      if(t.id!==id || t.status==="CLOSED") return t;
      const win = Math.random()>.4;
      const pnl_ = win ? Math.round(Math.random()*5000+500) : -Math.round(Math.random()*3000+300);
      setPnl(p=>p+pnl_);
      return {...t, status:"CLOSED", pnl:pnl_};
    }));
  };

  return (
    <div className="page"><div className="page-inner">
      <div className="page-head">
        <div className="page-title">🎯 Paper Trade</div>
        <div className="page-sub">Practice with virtual money — zero real risk. Margin: {fmtMargin(margin)}</div>
      </div>
      <div className="paper-stats">
        {[
          {l:"Virtual Balance", v:fmtMargin(Math.max(0,balance)), c:"var(--accent)"},
          {l:"Total P&L",       v:(pnl>=0?"+":"")+`₹${Math.abs(pnl).toLocaleString("en-IN")}`, c:pnl>=0?"var(--green)":"var(--red)"},
          {l:"Trades Today",    v:trades.length, c:"var(--text)"},
        ].map((k,i)=>(
          <div key={i} className="stat-card">
            <div className="stat-lbl">{k.l}</div>
            <div className="stat-val" style={{color:k.c,fontSize:16}}>{k.v}</div>
          </div>
        ))}
      </div>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
        <span style={{fontFamily:"var(--head)",fontWeight:800,fontSize:14}}>Simulated Trades</span>
        <button onClick={addTrade}
          style={{background:"var(--accent)",color:"#000",border:"none",borderRadius:5,padding:"6px 14px",fontFamily:"var(--mono)",fontSize:10,fontWeight:700,cursor:"pointer"}}>
          + Simulate Trade
        </button>
      </div>
      {trades.length===0 ? (
        <div className="empty"><div className="empty-icon">📋</div>Click "+ Simulate Trade" to practice with virtual money</div>
      ) : (
        <div className="signals-grid">
          {trades.map(t=>(
            <div key={t.id} className="sig-card">
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
                <span style={{fontWeight:700,fontSize:11}}>{t.strategy}</span>
                <span className="badge" style={{background:t.status==="OPEN"?"rgba(56,189,248,.1)":"rgba(74,96,128,.1)",color:t.status==="OPEN"?"var(--accent)":"var(--muted)",border:`1px solid ${t.status==="OPEN"?"rgba(56,189,248,.3)":"var(--border2)"}`}}>{t.status}</span>
              </div>
              <div style={{fontSize:9,color:"var(--muted)",marginBottom:6}}>
                <span style={{color:t.direction==="LONG"?"var(--green)":"var(--red)",fontWeight:700}}>{t.direction}</span>
                · Entry {t.entry} · {t.lots} lots · {t.time}
              </div>
              {t.status==="CLOSED" && (
                <div style={{fontSize:11,fontWeight:700,color:t.pnl>=0?"var(--green)":"var(--red)"}}>
                  {t.pnl>=0?"+":""}₹{Math.abs(t.pnl).toLocaleString("en-IN")}
                </div>
              )}
              {t.status==="OPEN" && (
                <button onClick={()=>closeTrade(t.id)}
                  style={{width:"100%",background:"rgba(248,113,113,.1)",color:"var(--red)",border:"1px solid rgba(248,113,113,.3)",borderRadius:4,padding:"4px 8px",fontFamily:"var(--mono)",fontSize:9,cursor:"pointer",marginTop:4}}>
                  Close Position
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div></div>
  );
};

// ════════════════════════════════════════════════════════════════
//  SUBSCRIPTION PAGE
// ════════════════════════════════════════════════════════════════
const PLANS_DATA = [
  { id:"free",    name:"Free",    price:"₹0",      period:"/mo",  color:"#64748b",
    features:["Paper trading only","5 delayed signals/day","NIFTY F&O only","Community support"] },
  { id:"starter", name:"Starter", price:"₹2,999",  period:"/mo",  color:"#34d399",
    features:["20 live signals","NIFTY + Crypto","NSE option chain","Email support","Calendar & Condor"] },
  { id:"pro",     name:"Pro",     price:"₹7,999",  period:"/mo",  color:"#38bdf8", featured:true,
    features:["50 live signals","All 4 markets","All 7 strategies","Backtest access","Priority support","Regime alerts"] },
  { id:"elite",   name:"Elite",   price:"₹19,999", period:"/mo",  color:"#f472b6",
    features:["Unlimited signals","All markets","API access","1-on-1 strategy call","Custom alerts","Dedicated manager"] },
];

const PlansPage = () => (
  <div className="page"><div className="page-inner">
    <div className="page-head">
      <div className="page-title">💎 Subscription Plans</div>
      <div className="page-sub">Professional NIFTY/BANKNIFTY/Crypto signal platform — built by traders, for traders</div>
    </div>
    <div className="plans-grid">
      {PLANS_DATA.map(p=>(
        <div key={p.id} className={`plan-card${p.featured?" featured":""}`}
          style={{borderColor:p.featured?p.color:"var(--border)",borderWidth:p.featured?2:1,
            position:"relative",paddingTop:p.featured?20:16}}>
          {p.featured && (
            <div style={{position:"absolute",top:-10,left:"50%",transform:"translateX(-50%)",
              background:p.color,color:"#000",padding:"2px 12px",borderRadius:10,
              fontSize:8,fontWeight:800,textTransform:"uppercase",letterSpacing:".1em",whiteSpace:"nowrap"}}>
              MOST POPULAR
            </div>
          )}
          <div style={{textAlign:"center",marginBottom:14}}>
            <div style={{fontFamily:"var(--head)",fontSize:16,fontWeight:800,color:p.color,marginBottom:4}}>{p.name}</div>
            <div style={{fontFamily:"var(--head)",fontSize:24,fontWeight:800}}>{p.price}<span style={{fontSize:11,color:"var(--muted)",fontFamily:"var(--mono)"}}>{p.period}</span></div>
          </div>
          <ul style={{listStyle:"none",marginBottom:14}}>
            {p.features.map((f,i)=>(
              <li key={i} style={{fontSize:10,color:"var(--text)",padding:"5px 0",borderBottom:"1px solid var(--border)",display:"flex",gap:7}}>
                <span style={{color:p.color}}>✓</span>{f}
              </li>
            ))}
          </ul>
          <button style={{width:"100%",background:p.featured?p.color:"transparent",
            color:p.featured?"#000":p.color,border:`1px solid ${p.color}`,
            borderRadius:5,padding:"8px",fontFamily:"var(--mono)",fontSize:10,fontWeight:700,cursor:"pointer"}}>
            {p.id==="free"?"Current Plan":"Upgrade"}
          </button>
        </div>
      ))}
    </div>
    <div style={{marginTop:16,padding:"12px 14px",background:"rgba(56,189,248,.05)",border:"1px solid rgba(56,189,248,.15)",borderRadius:7,fontSize:10,color:"var(--muted)"}}>
      💳 Payments via Razorpay · ₹ INR billing · Cancel anytime · 7-day free trial on Starter and above
    </div>
  </div></div>
);

// ════════════════════════════════════════════════════════════════
//  MAIN APP SHELL
// ════════════════════════════════════════════════════════════════
const AppShell = ({ token, user, margin, onMarginUpdate, onLogout }) => {
  const [activeMkt,   setActiveMkt]   = useState("EQUITY_INDIA");
  const [activeStrat, setActiveStrat] = useState("ALL");
  const [signals,     setSignals]     = useState({});
  const prices = useLivePrices();
  const wsRef  = useRef(null);

  // Seed initial signals for each market
  useEffect(() => {
    const init = {};
    ["EQUITY_INDIA","NIFTY_FO","BANKNIFTY_FO","CRYPTO"].forEach(mkt => {
      init[mkt] = Array.from({length:8}, ()=>makeSig(mkt));
    });
    setSignals(init);

    // Add new signals periodically
    const iv = setInterval(() => {
      ["EQUITY_INDIA","NIFTY_FO","BANKNIFTY_FO","CRYPTO"].forEach(mkt => {
        setSignals(prev => ({
          ...prev,
          [mkt]: [makeSig(mkt), ...(prev[mkt]||[])].slice(0,30),
        }));
      });
    }, 6000);
    return () => clearInterval(iv);
  }, []);

  // WebSocket
  useEffect(() => {
    const ws = new WebSocket(WS);
    wsRef.current = ws;
    ws.onmessage = e => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type==="signal" && msg.data) {
          const d = msg.data;
          const mkt = d.market==="crypto"?"CRYPTO":d.market==="BANKNIFTY"?"BANKNIFTY_FO":d.market==="nifty"?"NIFTY_FO":"EQUITY_INDIA";
          const sig = { id:d.symbol||Math.random().toString(36).slice(2,8), market:mkt,
            strategy:d.strategy||"SIGNAL", risk:d.risk||"MED", score:d.score||70,
            direction:d.direction||"LONG", detail:d.reason||"", entry_at:d.entry_at||0,
            target:d.target_at||0, sl:d.sl_at||0, lots:d.lots_suggested||2,
            near_bid:d.near_bid||0, near_ask:d.near_ask||0, far_bid:d.far_bid||0, far_ask:d.far_ask||0,
            time:new Date().toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",second:"2-digit"}),
            expanded:false, nse_enhanced:true, pcr:0.87, support:23700, resistance:24100 };
          setSignals(prev=>({...prev,[mkt]:[sig,...(prev[mkt]||[])].slice(0,30)}));
        }
      } catch{}
    };
    return () => ws.close();
  }, []);

  const toggleSig = useCallback((id) => {
    setSignals(prev => {
      const next = {...prev};
      Object.keys(next).forEach(mkt => {
        next[mkt] = next[mkt].map(s=>s.id===id?{...s,expanded:!s.expanded}:s);
      });
      return next;
    });
  }, []);

  const mktSignals = signals[activeMkt] || [];
  const mktDef     = SIDEBAR_NAV.find(n=>n.id===activeMkt);
  const mktColor   = mktDef?.color || "#38bdf8";

  const isMarketPage = ["EQUITY_INDIA","NIFTY_FO","BANKNIFTY_FO","CRYPTO"].includes(activeMkt);

  return (
    <div className="app">
      {/* ── SIDEBAR ── */}
      <div className="sidebar">
        <div className="sb-head">
          <div className="sb-logo">AlgoTrade</div>
          <div className="sb-sub">NIFTY · BANKNIFTY · CRYPTO</div>
        </div>

        <div className="sb-scroll">
          {/* Market sections */}
          {SIDEBAR_NAV.filter(n=>!["PAPER","PLANS","ANALYTICS"].includes(n.id)).map(nav => (
            <div key={nav.id} className="sb-section">
              <div className={`sb-item${activeMkt===nav.id?" active":""}`}
                onClick={()=>{ setActiveMkt(nav.id); setActiveStrat("ALL"); }}>
                <div className="sb-icon">{nav.icon}</div>
                <span className="sb-label" style={{color:activeMkt===nav.id?nav.color:undefined}}>{nav.label}</span>
                {signals[nav.id]?.length ? (
                  <span className="sb-badge" style={{background:nav.color+"20",color:nav.color,border:`1px solid ${nav.color}40`}}>
                    {signals[nav.id].length}
                  </span>
                ) : null}
              </div>
              {activeMkt===nav.id && nav.strategies.map(st => (
                <div key={st.id} className={`sb-strat${activeStrat===st.id?" active":""}`}
                  onClick={e=>{ e.stopPropagation(); setActiveStrat(activeStrat===st.id?"ALL":st.id); }}>
                  <div style={{width:4,height:4,borderRadius:"50%",background:RISK_C[st.risk]?.color||"var(--muted)",flexShrink:0}}/>
                  <span className="sb-strat-label">{st.label}</span>
                  <span style={{fontSize:7,color:RISK_C[st.risk]?.color||"var(--muted)",opacity:.7}}>{st.risk}</span>
                </div>
              ))}
              {activeMkt===nav.id && <div className="sb-divider"/>}
            </div>
          ))}

          {/* Utility pages */}
          <div className="sb-divider" style={{margin:"5px 0"}}/>
          {[
            {id:"PAPER",    icon:"🎯", label:"Paper Trade",  color:"#a78bfa"},
            {id:"PLANS",    icon:"💎", label:"Subscription", color:"#f472b6"},
            {id:"ANALYTICS",icon:"📊", label:"Analytics",    color:"#64748b"},
          ].map(u=>(
            <div key={u.id} className={`sb-item${activeMkt===u.id?" active":""}`} onClick={()=>setActiveMkt(u.id)}>
              <div className="sb-icon">{u.icon}</div>
              <span className="sb-label" style={{color:activeMkt===u.id?u.color:undefined}}>{u.label}</span>
            </div>
          ))}
        </div>

        {/* Margin pill at bottom of sidebar */}
        <div className="sb-margin-pill" onClick={()=>setActiveMkt("MARGIN")}>
          <div>
            <div style={{fontSize:8,color:"var(--muted)",textTransform:"uppercase",letterSpacing:".08em"}}>Session Margin</div>
            <div style={{fontFamily:"var(--head)",fontWeight:800,color:"var(--accent)",fontSize:13,marginTop:1}}>{fmtMargin(margin)}</div>
          </div>
          <div style={{color:"var(--dim)",fontSize:11}}>✎</div>
        </div>

        <div className="sb-footer">
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
            <div>
              <div style={{fontSize:10,fontWeight:700}}>{user?.name}</div>
              <div style={{fontSize:9,color:"var(--muted)"}}>{user?.plan||"pro"} plan</div>
            </div>
            <button onClick={onLogout}
              style={{background:"none",border:"1px solid var(--border2)",color:"var(--muted)",borderRadius:4,padding:"3px 8px",cursor:"pointer",fontSize:9,fontFamily:"var(--mono)"}}>
              Exit
            </button>
          </div>
        </div>
      </div>

      {/* ── MAIN CONTENT ── */}
      <div className="main">
        {/* Topbar */}
        <div className="topbar">
          {isMarketPage && (
            <div style={{display:"flex",alignItems:"center",gap:6}}>
              <div style={{width:8,height:8,borderRadius:"50%",background:mktColor}}/>
              <span style={{fontFamily:"var(--head)",fontWeight:800,fontSize:13,color:mktColor}}>{mktDef?.label}</span>
            </div>
          )}
          <div className="tb-pill"><div className="live-dot"/><span style={{color:"var(--muted)"}}>LIVE</span></div>
          <span style={{flex:1}}/>
          <div className="tb-pill">VIX <span style={{color:prices.vix<16?"var(--green)":prices.vix<19?"var(--yellow)":"var(--orange)",fontWeight:700,marginLeft:4}}>{prices.vix}</span></div>
          <div className="tb-pill">NIFTY <span style={{color:"var(--green)",fontWeight:700,marginLeft:4}}>{prices.nifty.toLocaleString("en-IN")}</span></div>
          <div className="tb-pill">BANK <span style={{color:"var(--yellow)",fontWeight:700,marginLeft:4}}>{prices.bank.toLocaleString("en-IN")}</span></div>
          <div className="tb-pill">BTC <span style={{color:"var(--orange)",fontWeight:700,marginLeft:4}}>${prices.btc.toLocaleString("en-US")}</span></div>
        </div>

        {/* Page content */}
        {activeMkt==="MARGIN"    && <MarginPage margin={margin} onUpdate={onMarginUpdate}/>}
        {activeMkt==="ANALYTICS" && <AnalyticsPage token={token}/>}
        {activeMkt==="PAPER"     && <PaperPage margin={margin}/>}
        {activeMkt==="PLANS"     && <PlansPage/>}
        {isMarketPage && (
          <MarketPage
            market={activeMkt}
            activeStrat={activeStrat}
            signals={mktSignals}
            onToggle={toggleSig}
            prices={prices}
            margin={margin}
          />
        )}
      </div>
    </div>
  );
};

// ════════════════════════════════════════════════════════════════
//  MARGIN SETUP SCREEN
// ════════════════════════════════════════════════════════════════
const MarginSetup = ({ user, onConfirm }) => {
  const [raw, setRaw]     = useState("");
  const [parsed, setParsed] = useState(null);
  const [step, setStep]   = useState(1);
  const [err, setErr]     = useState("");

  const parse = v => {
    const s=v.replace(/,/g,"").trim().toUpperCase();
    if(!s) return null;
    if(s.endsWith("CR")||s.endsWith("C")) return parseFloat(s)*10_000_000;
    if(s.endsWith("L")) return parseFloat(s)*100_000;
    const n=parseFloat(s); return isNaN(n)?null:n;
  };

  const next = () => {
    if(!parsed||parsed<=0){setErr("Enter a valid amount."); return;}
    if(parsed<10000){setErr("Minimum ₹10,000."); return;}
    if(step===1){setStep(2); setErr(""); return;}
    onConfirm(parsed);
  };

  const INST=[{n:"NIFTY",m:80000,c:"#34d399"},{n:"BANKNIFTY",m:90000,c:"#fbbf24"},{n:"BTCUSD",m:5000,c:"#fb923c"}];

  return (
    <div className="setup-wrap">
      <div style={{position:"absolute",inset:0,backgroundImage:"linear-gradient(rgba(56,189,248,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,.025) 1px,transparent 1px)",backgroundSize:"55px 55px"}}/>
      <div className="setup-card">
        {/* Steps */}
        <div style={{display:"flex",borderBottom:"1px solid var(--border)"}}>
          {["Set Margin","Confirm"].map((s,i)=>(
            <div key={i} className={`step-tab${step===i+1?" done":""}`}>{i+1}. {s}</div>
          ))}
        </div>

        <div style={{padding:28}}>
          <div style={{textAlign:"center",marginBottom:24}}>
            <div style={{fontFamily:"var(--head)",fontSize:32,fontWeight:800,background:"linear-gradient(135deg,#38bdf8,#34d399)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent",backgroundClip:"text",color:"transparent"}}>AlgoTrade</div>
            <div style={{color:"var(--muted)",fontSize:11,marginTop:2}}>Welcome back, {user?.name?.split(" ")[0]}! Set today's margin.</div>
          </div>

          {step===1 && <>
            <div style={{fontSize:9,color:"var(--muted)",textTransform:"uppercase",letterSpacing:".09em",marginBottom:7}}>Quick Select</div>
            <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:16}}>
              {PRESETS.map(p=>(
                <button key={p.label} className={`preset-btn${parsed===p.value?" active":""}`} onClick={()=>{setParsed(p.value);setRaw("");}}>
                  {p.label}
                </button>
              ))}
            </div>
            <div style={{fontSize:9,color:"var(--muted)",textTransform:"uppercase",letterSpacing:".09em",marginBottom:6}}>Or Enter Amount</div>
            <div style={{position:"relative",marginBottom:8}}>
              <span style={{position:"absolute",left:10,top:"50%",transform:"translateY(-50%)",color:"var(--muted)",fontSize:14}}>₹</span>
              <input className="auth-input" value={raw} onChange={e=>{setRaw(e.target.value);setParsed(parse(e.target.value));setErr("");}}
                onKeyDown={e=>e.key==="Enter"&&next()} placeholder="e.g. 50L or 5000000" style={{paddingLeft:26}}/>
            </div>
            {parsed && !err && <div style={{fontSize:10,color:"var(--green)",marginBottom:8}}>✓ {fmtMargin(parsed)} detected</div>}
            {err && <div style={{fontSize:10,color:"var(--red)",marginBottom:8}}>{err}</div>}
            <div style={{fontSize:9,color:"var(--dim)",marginBottom:16}}>Accepts: 50L · 25L · 1CR · 5000000</div>
          </>}

          {step===2 && parsed && <>
            <div style={{background:"var(--bg3)",border:"1px solid var(--border2)",borderRadius:8,padding:"14px 18px",marginBottom:16,textAlign:"center"}}>
              <div style={{fontSize:9,color:"var(--muted)",marginBottom:3,textTransform:"uppercase",letterSpacing:".09em"}}>Session Margin</div>
              <div style={{fontFamily:"var(--head)",fontSize:32,fontWeight:800,color:"var(--accent)"}}>{fmtMargin(parsed)}</div>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:8,marginBottom:16}}>
              {INST.map((im,i)=>{
                const ml=Math.floor(parsed/im.m);
                return (
                  <div key={i} style={{background:"var(--bg3)",borderRadius:6,padding:"8px 10px",border:`1px solid ${im.c}20`,textAlign:"center"}}>
                    <div style={{fontSize:10,fontWeight:700,color:im.c,marginBottom:2}}>{im.n}</div>
                    <div style={{fontFamily:"var(--head)",fontWeight:800,fontSize:16,color:ml>0?"var(--text)":"var(--red)"}}>{ml>0?ml+" lots":"—"}</div>
                    <div style={{fontSize:8,color:"var(--dim)"}}>₹{im.m.toLocaleString("en-IN")}/lot</div>
                  </div>
                );
              })}
            </div>
            <div style={{background:"rgba(56,189,248,.06)",border:"1px solid rgba(56,189,248,.15)",borderRadius:6,padding:"9px 12px",fontSize:9,color:"var(--accent)",lineHeight:1.6,marginBottom:16}}>
              ⚡ Actual lots per signal vary dynamically by score, VIX, regime, and strategy risk. Always 1 lot minimum.
            </div>
            <button className="preset-btn" onClick={()=>setStep(1)} style={{marginRight:10}}>← Change</button>
          </>}

          <button onClick={next}
            style={{background:"var(--accent)",color:"#000",border:"none",borderRadius:6,padding:"10px 20px",fontFamily:"var(--mono)",fontSize:11,fontWeight:700,cursor:"pointer",width:step===2?"auto":"100%"}}>
            {step===1?"Preview →":"Confirm & Start Trading"}
          </button>
        </div>
      </div>
    </div>
  );
};

// ════════════════════════════════════════════════════════════════
//  AUTH PAGE
// ════════════════════════════════════════════════════════════════
const AuthPage = ({ onLogin }) => {
  const [isReg, setReg] = useState(false);
  const [form, setForm]  = useState({name:"",email:"",password:""});
  const [err, setErr]    = useState("");
  const [loading, setL]  = useState(false);
  const set = (k,v) => setForm(f=>({...f,[k]:v}));

  const submit = async () => {
    setL(true); setErr("");
    try {
      const r = await fetch(`${API}${isReg?"/auth/register":"/auth/login"}`,{
        method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(form)
      }).then(r=>r.json());
      if(r.token) onLogin(r.token,r.user);
      else setErr(r.detail||"Failed. Is the backend running on port 8000?");
    } catch { setErr("Cannot connect. Make sure backend is running: python app/backend/main.py"); }
    setL(false);
  };

  return (
    <div className="auth-wrap">
      <div className="auth-grid"/>
      <div style={{position:"relative",zIndex:1}}>
        <div className="auth-logo">AlgoTrade</div>
        <div style={{textAlign:"center",color:"var(--muted)",fontSize:11,marginBottom:24}}>NIFTY · BANKNIFTY · CRYPTO · Multi-Market Signals</div>
        <div className="auth-card">
          <div style={{fontFamily:"var(--head)",fontSize:16,fontWeight:700,marginBottom:18}}>{isReg?"Create Account":"Sign In"}</div>
          {isReg && (
            <div style={{marginBottom:12}}>
              <div style={{fontSize:9,color:"var(--muted)",marginBottom:5,textTransform:"uppercase",letterSpacing:".09em"}}>Full Name</div>
              <input className="auth-input" value={form.name} onChange={e=>set("name",e.target.value)} placeholder="Rahul Sharma"/>
            </div>
          )}
          <div style={{marginBottom:12}}>
            <div style={{fontSize:9,color:"var(--muted)",marginBottom:5,textTransform:"uppercase",letterSpacing:".09em"}}>Email</div>
            <input className="auth-input" type="email" value={form.email} onChange={e=>set("email",e.target.value)} placeholder="you@example.com"/>
          </div>
          <div style={{marginBottom:16}}>
            <div style={{fontSize:9,color:"var(--muted)",marginBottom:5,textTransform:"uppercase",letterSpacing:".09em"}}>Password</div>
            <input className="auth-input" type="password" value={form.password} onChange={e=>set("password",e.target.value)} onKeyDown={e=>e.key==="Enter"&&submit()} placeholder="••••••••"/>
          </div>
          {err && <div style={{color:"var(--red)",fontSize:10,marginBottom:12,padding:"6px 10px",background:"rgba(248,113,113,.1)",borderRadius:4}}>{err}</div>}
          <button className="auth-btn" onClick={submit} disabled={loading} style={{marginBottom:12}}>
            {loading?"…":isReg?"Create Account":"Sign In"}
          </button>
          <div style={{textAlign:"center",fontSize:10,color:"var(--muted)"}}>
            {isReg?"Already have an account? ":"New here? "}
            <span style={{color:"var(--accent)",cursor:"pointer"}} onClick={()=>setReg(r=>!r)}>{isReg?"Sign In":"Register Free"}</span>
          </div>
          <div style={{marginTop:14,paddingTop:12,borderTop:"1px solid var(--border)",fontSize:9,color:"var(--dim)",textAlign:"center"}}>
            Demo: demo@algotrade.in / demo123
          </div>
        </div>
      </div>
    </div>
  );
};

// ════════════════════════════════════════════════════════════════
//  ROOT
// ════════════════════════════════════════════════════════════════
export default function App() {
  const [token,  setToken]  = useState(()=>localStorage.getItem("at_tok")||"");
  const [user,   setUser]   = useState(()=>{ try{return JSON.parse(localStorage.getItem("at_usr")||"null")}catch{return null} });
  const [margin, setMargin] = useState(null); // always reset each session

  const login = (t,u) => {
    localStorage.setItem("at_tok",t); localStorage.setItem("at_usr",JSON.stringify(u));
    setToken(t); setUser(u); setMargin(null);
  };
  const logout = () => {
    localStorage.removeItem("at_tok"); localStorage.removeItem("at_usr");
    setToken(""); setUser(null); setMargin(null);
  };

  return (
    <>
      <G/>
      {!token||!user  ? <AuthPage onLogin={login}/>
      : !margin        ? <MarginSetup user={user} onConfirm={setMargin}/>
      : <AppShell token={token} user={user} margin={margin} onMarginUpdate={setMargin} onLogout={logout}/>}
    </>
  );
}