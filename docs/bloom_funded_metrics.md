# Bloom Funded Challenge Analytics

**Analysis Period & Details**
- **Initial Balance**: $25,000
- **Final Balance**: $36,205.31
- **Net Profit**: $11,205.31 (44.82%)
- **Total Trades Evaluated**: 183
- **Instrument**: Crash 1000 Index

> [!WARNING]
> **LOT SIZE VIOLATION DETECTED**
> According to Section 1.2.8 of the BloomFunded Terms, the maximum allowed lot size for Crash 1000 on a $25,000 account is **6.0**. The backtest executed almost all trades using a combined lot size of **10.00** (e.g. 3.0 + 3.0 + 4.0). This strategy configuration would result in an immediate account suspension. The limit applies to maximum lot per open position.

---

## 1. Challenge Parameters & Profit Targets (Section 1.2.5)

| Metric | Required / Limit | Result | Status |
|--------|----------------|--------|--------|
| **1-Step Profit Target** | 10% ($2,500) | 44.82% ($11,205.31) | ✅ PASS |
| **2-Step P1 Target** | 8% ($2,000) | 44.82% ($11,205.31) | ✅ PASS |
| **2-Step P2 Target** | 5% ($1,250) | 44.82% ($11,205.31) | ✅ PASS |

## 2. Drawdowns (Section 1.2.1 & 1.2.2)

- **1-Step Max Drawdown (8% Trailing)**: The strategy's maximum drawdown did not breach the 8% ($2,000) trailing limit below the highest water mark. ✅ PASS
- **2-Step Max Drawdown (6% Static)**: The strategy's maximum drawdown did not breach the fixed $23,500 limit. ✅ PASS
- **Daily Drawdown (4%)**: The strategy did not incur a daily equity drop exceeding 4% ($1,000) in a single day. ✅ PASS

> [!TIP]
> Both Daily Drawdown and Maximum Drawdown serve as vital tools. Your strategy's current risk limits are tight enough to pass both challenge models.

## 3. Position Sizing & Limits (Section 1.2.7 & 1.2.8)

| Metric | Rule Limit | Strategy Max | Status |
|--------|------------|--------------|--------|
| **Max Open Positions per Asset** | 5 | 1 | ✅ PASS |
| **Max Open Positions per Account** | 13 | 1 | ✅ PASS |
| **Max Trades in a Single Day** | N/A | 5 | ✅ N/A |
| **Maximum Lot Size (Crash 1000)** | 6.00 | 10.00 | ❌ FAIL |

> [!CAUTION]
> You must reduce your position sizing algorithm to ensure aggregate open lots `<= 6.0` when trading Crash 1000 on a 25k account.

## 4. Minimum Trading Days (Section 1.2.3)

*Rule: Minimum of 4 active days where a minimum of 0.5% profit ($125 for a $25,000 account) is made.*
- **Qualifying Days Achieved**: 52 days
- **Required**: 4 days
- **Status**: ✅ PASS

## 5. Consistency Rules (Section 1.2.4)

*Rule: No single trading day may generate a profit greater than X% of the applicable profit target.*
- **Max Single Day Profit Achieved**: $810.58

| Challenge Model | Max Allowed Daily Profit | Result | Status |
|-----------------|--------------------------|--------|--------|
| **1-Step (35% of $2,500 target)** | $875.00 | $810.58 | ✅ PASS |
| **2-Step P1 (40% of $2,000 target)** | $800.00 | $810.58 | ❌ FAIL |

> [!WARNING]
> Your highest single-day profit of $810.58 exceeds the $800 limit for the 2-Step Phase 1 Challenge. You would breach the consistency rule and fail the payout qualification unless you reduce your position sizing or use the consistency rule add-on to remove this restriction.

## 6. Prohibited Trading Practices (Section 1.2.6 & 2.1)

- **Excessive Scalping (Gambling)**: *Executing 50% or more of trades in under 2 minutes.*
  - **Strategy Result**: 0 out of 183 trades (0.00%) were under 2 minutes. ✅ PASS
- **Hedging**: Not permitted. The strategy does not hedge. ✅ PASS
- **Latency / Gap Trading**: The strategy relies on standard technical analysis and does not use high-frequency manipulation. ✅ PASS

---

## 7. Payout Prediction (Funded Phase)

According to Section 1.2: **Max Payout Cap 5% (only 1st payout)** and Section 1.2.4 **Consistency Rule**.

- **Total Account Profit**: $11,205.31
- **Max Single Day Profit**: $810.58
- **Required Payout Request to Pass Consistency**: $810.58 / 0.35 = **$2,315.94**
- **1st Payout Disbursement Cap (5% of $25,000)**: **$1,250.00**

> [!TIP]
> **Funded Phase Payout Strategy**
> Since your highest single-day profit is $810.58, the 35% consistency rule dictates you must request a payout of at least **$2,316** to remain compliant ($810.58 is 35% of $2,316). 
> Because your strategy generated $11,205 in profit, you can easily request this amount. 
> Keep in mind that for your **first payout**, BloomFunded will cap the actual disbursement at 5% of your initial balance (**$1,250**). The remaining requested profit will remain in your account and can be withdrawn in subsequent, uncapped payout cycles.
