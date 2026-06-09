# kite_source — Full Platform Backtest Report

**Period:** `2021-01-01` → `2025-12-31` (5 years)
**Available margin:** ₹50L (₹5,000,000)
**Instruments:** NIFTY · BANKNIFTY · FINNIFTY · Equity (top 30 stocks)
**Lot sizes:** NIFTY=50 · BANKNIFTY=15 · FINNIFTY=40 · Equity=by capital
**Split:** 70% in-sample / 30% out-of-sample (strict chronological)
**Walk-forward windows:** 5
**Data source:** Real NSE F&O Bhavcopy — no mocks, no random values

---

## Performance Summary

| Strategy | Backtest Period | Total Trades | Win Rate | Profit Factor | Max Drawdown | Sharpe Ratio | Calmar Ratio | Total P&L | Avg P&L / Trade | Best Trade | Worst Trade | Avg Hold (days) | Signal Credibility |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PCR_MeanReversion_BANKNIFTY [IN-SAMPLE] | 2024-06-28 → 2024-07-01 | 1 | 100.0% | inf | 0.00% | 0.00 | 0.00 | ₹8,602 | ₹8,602 | ₹8,602 | ₹8,602 | 3.0 | UNRELIABLE (insufficient trades) |
| IronFly_BANKNIFTY [OUT-OF-SAMPLE] | 2025-07-29 → 2025-07-31 | 1 | 0.0% | 0.00 | 0.00% | 0.00 | 0.00 | ₹-28,617 | ₹-28,617 | ₹-28,617 | ₹-28,617 | 2.0 | UNRELIABLE (insufficient trades) |
| IronCondor_BANKNIFTY [IN-SAMPLE] | 2024-01-03 → 2025-05-28 | 31 | 58.1% | 5.80 | 40.82% | 7.63 | 9999.00 | ₹7,399,927 | ₹238,707 | ₹1,442,080 | ₹-717,916 | 4.8 | STRONG |
| IronCondor_FINNIFTY [IN-SAMPLE] | 2024-01-09 → 2025-05-28 | 28 | 64.3% | 2.96 | 282.99% | 4.49 | 9999.00 | ₹6,307,151 | ₹225,255 | ₹2,522,461 | ₹-1,559,972 | 5.0 | MODERATE |
| IronCondor_NIFTY [IN-SAMPLE] | 2024-02-06 → 2025-05-15 | 19 | 42.1% | 1.31 | 110.92% | 1.31 | 9999.00 | ₹1,081,718 | ₹56,933 | ₹1,216,215 | ₹-1,186,664 | 2.1 | UNRELIABLE (random noise) |
| IronCondor_NIFTY [OUT-OF-SAMPLE] | 2025-06-10 → 2025-12-09 | 8 | 25.0% | 1.91 | 51.55% | 3.15 | 9999.00 | ₹1,202,248 | ₹150,281 | ₹1,272,072 | ₹-1,261,626 | 3.6 | UNRELIABLE (insufficient trades) |
| IronCondor_BANKNIFTY [OUT-OF-SAMPLE] | 2025-07-29 → 2025-11-25 | 4 | 75.0% | 0.54 | 190.39% | -3.31 | -999.00 | ₹-731,586 | ₹-182,896 | ₹809,847 | ₹-1,600,720 | 4.5 | UNRELIABLE (insufficient trades) |
| IronCondor_FINNIFTY [OUT-OF-SAMPLE] | 2025-07-29 → 2025-12-30 | 5 | 100.0% | inf | 0.00% | 50.00 | 9999.00 | ₹16,110 | ₹3,222 | ₹3,444 | ₹2,978 | 6.0 | UNRELIABLE (insufficient trades) |
| EMA_Crossover_NIFTY [IN-SAMPLE] | 2024-05-24 → 2025-05-28 | 4 | 50.0% | 0.05 | 2375.37% | -14.46 | -999.00 | ₹-191,270 | ₹-47,818 | ₹8,438 | ₹-103,262 | 115.2 | UNRELIABLE (insufficient trades) |
| EMA_Crossover_BANKNIFTY [IN-SAMPLE] | 2024-11-14 → 2024-11-27 | 1 | 0.0% | 0.00 | 0.00% | 0.00 | 0.00 | ₹-237,608 | ₹-237,608 | ₹-237,608 | ₹-237,608 | 13.0 | UNRELIABLE (insufficient trades) |

---

## Walk-Forward Validation

| Strategy | Period | Trades | Win Rate | Profit Factor | Sharpe | Credibility |
| --- | --- | --- | --- | --- | --- | --- |
| PCR_MeanReversion_W1 | N/A → N/A | 0 | 0.0% | 0.00 | 0.00 | UNRELIABLE (no trades) |
| PCR_MeanReversion_W2 | N/A → N/A | 0 | 0.0% | 0.00 | 0.00 | UNRELIABLE (no trades) |
| PCR_MeanReversion_W3 | N/A → N/A | 0 | 0.0% | 0.00 | 0.00 | UNRELIABLE (no trades) |
| PCR_MeanReversion_W4 | N/A → N/A | 0 | 0.0% | 0.00 | 0.00 | UNRELIABLE (no trades) |
| PCR_MeanReversion_W5 | 2024-06-28 → 2024-07-01 | 1 | 100.0% | inf | 0.00 | UNRELIABLE (insufficient trades) |
| IronFly_W1 | N/A → N/A | 0 | 0.0% | 0.00 | 0.00 | UNRELIABLE (no trades) |
| IronFly_W2 | N/A → N/A | 0 | 0.0% | 0.00 | 0.00 | UNRELIABLE (no trades) |
| IronFly_W3 | N/A → N/A | 0 | 0.0% | 0.00 | 0.00 | UNRELIABLE (no trades) |
| IronFly_W4 | N/A → N/A | 0 | 0.0% | 0.00 | 0.00 | UNRELIABLE (no trades) |
| IronFly_W5 | 2025-07-29 → 2025-07-31 | 1 | 0.0% | 0.00 | 0.00 | UNRELIABLE (insufficient trades) |
| IronCondor_W1 | 2024-01-03 → 2024-04-09 | 19 | 68.4% | 6.53 | 9.20 | WEAK |
| IronCondor_W2 | 2024-04-09 → 2024-07-22 | 19 | 52.6% | 1.03 | 0.17 | WEAK |
| IronCondor_W3 | 2024-07-18 → 2024-11-08 | 19 | 31.6% | 6.00 | 6.20 | UNRELIABLE (random noise) |
| IronCondor_W4 | 2024-11-12 → 2025-05-15 | 19 | 68.4% | 8.56 | 5.87 | WEAK |
| IronCondor_W5 | 2025-05-27 → 2025-12-30 | 19 | 63.2% | 0.84 | -0.61 | UNRELIABLE (random noise) |
| EMA_Crossover_W1 | 2024-05-24 → 2025-05-28 | 1 | 100.0% | inf | 0.00 | UNRELIABLE (insufficient trades) |
| EMA_Crossover_W2 | 2024-11-14 → 2024-11-27 | 1 | 0.0% | 0.00 | 0.00 | UNRELIABLE (insufficient trades) |
| EMA_Crossover_W3 | 2025-04-09 → 2025-05-28 | 1 | 0.0% | 0.00 | 0.00 | UNRELIABLE (insufficient trades) |
| EMA_Crossover_W4 | 2025-04-11 → 2025-04-17 | 1 | 0.0% | 0.00 | 0.00 | UNRELIABLE (insufficient trades) |
| EMA_Crossover_W5 | 2025-04-21 → 2025-05-28 | 1 | 100.0% | inf | 0.00 | UNRELIABLE (insufficient trades) |

---

## Position Sizing Logic

| Instrument | Lot Size | Margin per lot (approx) |
| --- | --- | --- |
| NIFTY | 50 units | ~₹230,000 (5× leverage) |
| BANKNIFTY | 15 units | ~₹156,000 (5× leverage) |
| FINNIFTY | 40 units | ~₹184,000 (5× leverage) |
| Equity | 2% of margin / price | ~₹100,000 per trade |

With ₹50L margin:
- Can run 21 concurrent NIFTY lots
- Can run 32 concurrent BANKNIFTY lots
- Equity: ₹1,00,000 per trade (2% risk rule)

---

## Signal Credibility Scale

| Rating | Criteria |
| --- | --- |
| STRONG | Win rate >55%, PF >1.5, Sharpe >1.0, ≥30 trades |
| MODERATE | Win rate >50%, PF >1.2, Sharpe >0.5, ≥20 trades |
| WEAK | Win rate >45%, PF >1.0, ≥10 trades |
| UNRELIABLE | Below thresholds — do not trade live |

---
_Generated by kite_source backtester. All data from NSE Bhavcopy archives._