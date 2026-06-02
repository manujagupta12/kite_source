# kite_source — Full Platform Backtest Report

**Period:** `2020-01-01` → `2025-12-31` (5 years)
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
| PCR_MeanReversion_FINNIFTY [IN-SAMPLE] | 2024-01-02 → 2025-05-26 | 179 | 54.2% | 0.31 | 120950.66% | -3.01 | -909.11 | ₹-2,225,338 | ₹-12,432 | ₹52,412 | ₹-378,618 | 6.4 | UNRELIABLE (random noise) |
| PCR_MeanReversion_NIFTY [IN-SAMPLE] | 2024-01-03 → 2025-05-29 | 173 | 56.1% | 0.31 | 26105.64% | -2.76 | -4601.12 | ₹-2,440,436 | ₹-14,107 | ₹48,250 | ₹-492,974 | 4.5 | UNRELIABLE (random noise) |
| PCR_MeanReversion_BANKNIFTY [IN-SAMPLE] | 2024-01-03 → 2025-05-28 | 163 | 49.7% | 0.29 | 38745.55% | -3.24 | -2647.09 | ₹-2,079,748 | ₹-12,759 | ₹56,040 | ₹-313,736 | 7.9 | UNRELIABLE (random noise) |
| PCR_MeanReversion_NIFTY [OUT-OF-SAMPLE] | 2025-05-30 → 2025-12-31 | 81 | 44.4% | 0.28 | 3620.73% | -5.93 | -13193.98 | ₹-407,577 | ₹-5,032 | ₹40,067 | ₹-76,851 | 2.9 | UNRELIABLE (random noise) |
| PCR_MeanReversion_BANKNIFTY [OUT-OF-SAMPLE] | 2025-05-30 → 2025-12-31 | 44 | 45.5% | 0.36 | 3662.79% | -6.29 | -5025.93 | ₹-157,060 | ₹-3,570 | ₹11,090 | ₹-20,200 | 4.7 | UNRELIABLE (random noise) |
| PCR_MeanReversion_FINNIFTY [OUT-OF-SAMPLE] | 2025-06-02 → 2025-12-31 | 74 | 29.7% | 0.12 | 7606.06% | -3.96 | -14278.38 | ₹-913,638 | ₹-12,346 | ₹17,761 | ₹-427,999 | 4.5 | UNRELIABLE (random noise) |
| ATM_Strangle_BANKNIFTY [IN-SAMPLE] | 2024-01-02 → 2025-05-29 | 153 | 0.0% | 0.00 | -0.00% | -73.60 | -2213766578947368.00 | ₹-4,506,596 | ₹-29,455 | ₹-621 | ₹-47,891 | 3.7 | UNRELIABLE (random noise) |
| ATM_Strangle_FINNIFTY [IN-SAMPLE] | 2024-01-03 → 2025-05-29 | 104 | 1.0% | 0.02 | -0.00% | -40.83 | -1658412590625000.00 | ₹-3,369,473 | ₹-32,399 | ₹76,083 | ₹-49,964 | 4.5 | UNRELIABLE (random noise) |
| ATM_Strangle_NIFTY [IN-SAMPLE] | 2024-01-05 → 2025-05-29 | 80 | 0.0% | 0.00 | -0.00% | -92.02 | -2226346376470588.50 | ₹-4,505,701 | ₹-56,321 | ₹-2,032 | ₹-70,892 | 3.6 | UNRELIABLE (random noise) |
| ATM_Strangle_NIFTY [OUT-OF-SAMPLE] | 2025-06-09 → 2026-01-01 | 39 | 0.0% | 0.00 | -0.00% | -97.62 | -2708358990291262.00 | ₹-2,213,976 | ₹-56,769 | ₹-2,026 | ₹-66,547 | 4.5 | UNRELIABLE (random noise) |
| ATM_Strangle_BANKNIFTY [OUT-OF-SAMPLE] | 2025-06-19 → 2025-12-30 | 19 | 0.0% | 0.00 | -0.00% | -218.49 | -900497319587628.75 | ₹-693,240 | ₹-36,486 | ₹-33,325 | ₹-40,511 | 4.9 | UNRELIABLE (random noise) |
| ATM_Strangle_FINNIFTY [OUT-OF-SAMPLE] | 2025-06-19 → 2025-12-30 | 16 | 0.0% | 0.00 | -0.00% | -272.43 | -770808767010309.25 | ₹-593,400 | ₹-37,088 | ₹-33,694 | ₹-40,675 | 5.1 | UNRELIABLE (random noise) |
| EMA_Crossover_BANKNIFTY [IN-SAMPLE] | 2024-01-25 → 2025-05-09 | 66 | 69.7% | 3.51 | 35.37% | 3.58 | 905180.96 | ₹597,155 | ₹9,048 | ₹226,754 | ₹-189,008 | 16.9 | STRONG |
| EMA_Crossover_NIFTY [IN-SAMPLE] | 2024-02-07 → 2025-05-26 | 90 | 66.7% | 8.69 | 14.25% | 11.05 | 4158638.43 | ₹1,114,871 | ₹12,387 | ₹128,026 | ₹-13,520 | 14.7 | STRONG |
| EMA_Crossover_FINNIFTY [IN-SAMPLE] | 2024-02-07 → 2025-05-05 | 64 | 57.8% | 0.30 | 2915.58% | -3.22 | -16588.02 | ₹-869,396 | ₹-13,584 | ₹16,490 | ₹-328,277 | 16.3 | UNRELIABLE (random noise) |
| EMA_Crossover_NIFTY [OUT-OF-SAMPLE] | 2025-07-11 → 2026-01-01 | 36 | 66.7% | 2.99 | 44.24% | 4.32 | 1112653.12 | ₹339,910 | ₹9,442 | ₹133,248 | ₹-147,318 | 12.2 | STRONG |
| EMA_Crossover_BANKNIFTY [OUT-OF-SAMPLE] | 2025-08-01 → 2026-01-01 | 6 | 0.0% | 0.00 | -0.00% | -53.21 | -10905423529411.76 | ₹-6,621 | ₹-1,104 | ₹-600 | ₹-1,681 | 4.8 | UNRELIABLE (insufficient trades) |
| EMA_Crossover_FINNIFTY [OUT-OF-SAMPLE] | 2025-08-01 → 2026-01-01 | 8 | 0.0% | 0.00 | -0.00% | -9.40 | -928314635294117.75 | ₹-563,620 | ₹-70,452 | ₹-1,600 | ₹-293,496 | 7.0 | UNRELIABLE (insufficient trades) |
| Calendar_Spread_FINNIFTY [IN-SAMPLE] | 2024-05-29 → 2024-06-05 | 8 | 25.0% | 0.45 | 909.76% | -5.77 | -98184.41 | ₹-24,812 | ₹-3,102 | ₹10,212 | ₹-15,597 | 2.9 | UNRELIABLE (insufficient trades) |
| Calendar_Spread_BANKNIFTY [IN-SAMPLE] | 2024-06-04 → 2025-01-09 | 2 | 0.0% | 0.00 | -0.00% | -17.76 | -30785079452054.80 | ₹-26,754 | ₹-13,377 | ₹-1,411 | ₹-25,343 | 8.5 | UNRELIABLE (insufficient trades) |
| Calendar_Spread_BANKNIFTY [OUT-OF-SAMPLE] | 2025-09-26 → 2025-10-06 | 3 | 66.7% | 3.37 | 0.00% | 9.91 | 987022260000000.00 | ₹39,168 | ₹13,056 | ₹28,054 | ₹-16,496 | 5.7 | UNRELIABLE (insufficient trades) |

---

## Walk-Forward Validation

| Strategy | Period | Trades | Win Rate | Profit Factor | Sharpe | Credibility |
| --- | --- | --- | --- | --- | --- | --- |
| PCR_MeanReversion_W1 | 2024-01-02 → 2024-06-04 | 142 | 47.2% | 0.12 | -3.73 | UNRELIABLE (random noise) |
| PCR_MeanReversion_W2 | 2024-06-03 → 2024-11-04 | 142 | 50.0% | 0.24 | -3.33 | UNRELIABLE (random noise) |
| PCR_MeanReversion_W3 | 2024-10-21 → 2025-02-27 | 142 | 63.4% | 0.39 | -2.41 | UNRELIABLE (random noise) |
| PCR_MeanReversion_W4 | 2025-02-12 → 2025-08-08 | 142 | 49.3% | 0.56 | -1.41 | UNRELIABLE (random noise) |
| PCR_MeanReversion_W5 | 2025-07-21 → 2025-12-31 | 146 | 37.7% | 0.21 | -3.94 | UNRELIABLE (random noise) |
| ATM_Strangle_W1 | 2024-01-02 → 2024-03-27 | 82 | 1.2% | 0.02 | -34.11 | UNRELIABLE (random noise) |
| ATM_Strangle_W2 | 2024-03-22 → 2024-06-25 | 82 | 0.0% | 0.00 | -44.26 | UNRELIABLE (random noise) |
| ATM_Strangle_W3 | 2024-06-20 → 2024-09-11 | 82 | 0.0% | 0.00 | -42.82 | UNRELIABLE (random noise) |
| ATM_Strangle_W4 | 2024-09-09 → 2025-05-23 | 82 | 0.0% | 0.00 | -38.33 | UNRELIABLE (random noise) |
| ATM_Strangle_W5 | 2025-05-23 → 2026-01-01 | 83 | 0.0% | 0.00 | -27.83 | UNRELIABLE (random noise) |
| EMA_Crossover_W1 | 2024-01-25 → 2024-05-17 | 54 | 61.1% | 5.24 | 4.71 | STRONG |
| EMA_Crossover_W2 | 2024-04-18 → 2024-08-16 | 54 | 70.4% | 2.80 | 5.00 | STRONG |
| EMA_Crossover_W3 | 2024-07-18 → 2024-11-29 | 54 | 70.4% | 8.03 | 12.57 | STRONG |
| EMA_Crossover_W4 | 2024-11-13 → 2025-05-26 | 54 | 61.1% | 0.82 | -0.59 | UNRELIABLE (random noise) |
| EMA_Crossover_W5 | 2025-05-13 → 2026-01-01 | 54 | 46.3% | 0.60 | -1.49 | UNRELIABLE (random noise) |
| Calendar_Spread_W1 | 2024-05-29 → 2024-06-03 | 2 | 50.0% | 1.60 | 3.64 | UNRELIABLE (insufficient trades) |
| Calendar_Spread_W2 | 2024-05-30 → 2024-06-04 | 2 | 0.0% | 0.00 | -56.38 | UNRELIABLE (insufficient trades) |
| Calendar_Spread_W3 | 2024-05-31 → 2024-06-03 | 2 | 50.0% | 1.10 | 0.75 | UNRELIABLE (insufficient trades) |
| Calendar_Spread_W4 | 2024-06-03 → 2024-06-05 | 2 | 0.0% | 0.00 | -440.92 | UNRELIABLE (insufficient trades) |
| Calendar_Spread_W5 | 2024-06-04 → 2025-10-06 | 5 | 40.0% | 1.31 | 1.90 | UNRELIABLE (insufficient trades) |

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