Now I have the complete picture. `_checkedMul` is simply `return a * b` — it is NOT inside an `unchecked` block, so it uses Solidity 0.8+ checked arithmetic and reverts on overflow.

### Title
`_scaleWeightsToShares` mints unbounded shares for free when probe returns `need0=0, need1=0` — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

When `addLiquidityWeighted` probes a bin that has existing shares but zero token balances (a legitimate post-traversal pool state), the pool callback fires `LiquidityProbe(0, 0)`. `_scaleWeightsToShares` interprets both-zero as "both legs unconstrained" and sets `scaleWad = type(uint256).max`. The paying add then mints astronomically large shares for 0 tokens paid, permanently diluting existing LPs.

---

### Finding Description

**Step 1 — `need0=0, need1=0` is reachable.**

In `LiquidityLib.addLiquidity`, when a bin has `binTotalSharesVal > 0` but `token0BalanceScaled == 0` and `token1BalanceScaled == 0` (all tokens previously swapped out of the bin):

```solidity
// LiquidityLib.sol line 109-110
amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
// = Math.ceilDiv(0 * sharesToAdd, binTotalSharesVal) = 0
amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
// = 0
```

`totalToken0ToAddScaled = 0`, `totalToken1ToAddScaled = 0`. The probe callback fires `LiquidityProbe(0, 0)`. [1](#0-0) 

**Step 2 — `_scaleWeightsToShares` sets `scaleWad = type(uint256).max`.**

```solidity
// MetricOmmPoolLiquidityAdder.sol line 231-233
uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
uint256 scaleWad = scaleWad0 < scaleWad1 ? scaleWad0 : scaleWad1;
// scaleWad = type(uint256).max
``` [2](#0-1) 

**Step 3 — Huge shares are computed without overflow (for `w.shares[i] < WAD`).**

`Math.mulDiv` uses 512-bit intermediate arithmetic and does **not** silently overflow — it reverts if the result exceeds `uint256`. For `w.shares[i] = 1`:

```
Math.mulDiv(1, type(uint256).max, 1e18) = type(uint256).max / 1e18 ≈ 1.157e59
```

No overflow. The scaled shares array contains `≈ 1.157e59` shares. [3](#0-2) 

**Step 4 — The paying add succeeds with 0 tokens paid.**

The paying add calls `pool.addLiquidity` with `sharesToAdd ≈ 1.157e59`. Inside `LiquidityLib.addLiquidity`:

```solidity
_checkedMul(0, 1.157e59) = 0   // no overflow, token balances are 0
amount0Scaled = 0, amount1Scaled = 0
```

Because `amount0Added == 0 && amount1Added == 0`, the callback is **never triggered**:

```solidity
// LiquidityLib.sol line 144
if (amount0Added > 0 || amount1Added > 0) {
    // callback NOT called — no tokens pulled
}
```

`binTotalShares[binIdx] += 1.157e59` is written to storage. The attacker now holds a dominant fraction of the bin's shares for free. [4](#0-3) 

**Step 5 — Attacker drains tokens in batches after price moves back.**

When the price cursor re-enters the bin, tokens flow in. The attacker removes shares in batches sized to avoid `_checkedMul` overflow:

```solidity
// _checkedMul is checked arithmetic (separate function, outside unchecked block)
function _checkedMul(uint256 a, uint256 b) internal pure returns (uint256) {
    return a * b;  // reverts on overflow
}
```

For each batch of `X` shares removed: `amount0Scaled = T * X / binTotalSharesVal`. Since the attacker holds `≈ 1.157e59` of the total shares, they receive nearly all tokens. Existing LPs receive:

```
existing_LP_tokens = T * existing_LP_shares / (1.157e59 + existing_LP_shares) ≈ 0
``` [5](#0-4) 

---

### Impact Explanation

Existing LPs permanently lose their proportional claim on bin tokens. The attacker acquires a dominant share position for 0 cost, then drains the bin as tokens flow back in. This is direct loss of LP principal and pool insolvency for the affected bin.

---

### Likelihood Explanation

- **Bin with zero token balances** is a normal post-traversal state: any bin the price cursor has fully passed through has zero token balances but retains shares. This happens organically on active pools.
- **Attacker access**: requires the attacker to be allowlisted by the pool admin, or the pool to have `allowAllDepositors = true`. The latter is a common configuration for permissionless pools using the extension only for accounting.
- **Weight constraint**: attacker must use `w.shares[i] < WAD` (e.g., 1) to avoid `Math.mulDiv` reverting. This is trivially satisfied since `_validatePositiveWeights` only requires `> 0`. [6](#0-5) 

---

### Recommendation

In `_scaleWeightsToShares`, add an explicit guard for the `need0=0, need1=0` case:

```solidity
if (need0 == 0 && need1 == 0) revert ZeroTokensRequired();
```

Alternatively, treat both-zero as "no scaling" (`scaleWad = WAD`) so the original weight shares are used unchanged, which is the semantically correct behavior when the probe indicates no tokens are needed. [7](#0-6) 

---

### Proof of Concept

```solidity
// Setup: pool with bin 3 that has existing shares but zero token balances
// (achieved by swapping all tokens out of bin 3)

// Attacker calls addLiquidityWeighted with weight = 1 share in bin 3
LiquidityDelta memory w;
w.binIdxs = new int256[](1);
w.shares = new uint256[](1);
w.binIdxs[0] = 3;
w.shares[0] = 1; // tiny weight, < WAD

// Probe fires LiquidityProbe(0, 0) because bin 3 has zero token balances
// _scaleWeightsToShares: scaleWad = type(uint256).max
// scaled.shares[0] = Math.mulDiv(1, type(uint256).max, 1e18) ≈ 1.157e59

// Paying add: _checkedMul(0, 1.157e59) = 0, no callback, 0 tokens paid
// Attacker now holds 1.157e59 shares in bin 3

adder.addLiquidityWeighted(
    pool, attacker, 0, w,
    type(uint256).max, type(uint256).max,
    type(int8).min, 0, type(int8).max, type(uint104).max, ""
);

// Assert: attacker holds ~1.157e59 shares, paid 0 tokens
// Assert: existing LPs' removeLiquidity returns ~0 tokens
```

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L108-111)
```text
          } else {
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-155)
```text
      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
      }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L261-263)
```text
  function _checkedMul(uint256 a, uint256 b) internal pure returns (uint256) {
    return a * b;
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L226-243)
```text
  function _scaleWeightsToShares(LiquidityDelta calldata w, uint256 max0, uint256 max1, uint256 need0, uint256 need1)
    internal
    pure
    returns (LiquidityDelta memory out)
  {
    uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
    uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
    uint256 scaleWad = scaleWad0 < scaleWad1 ? scaleWad0 : scaleWad1;

    uint256 n = w.binIdxs.length;
    out.binIdxs = new int256[](n);
    out.shares = new uint256[](n);
    for (uint256 i; i < n; i++) {
      out.binIdxs[i] = w.binIdxs[i];
      out.shares[i] = Math.mulDiv(w.shares[i], scaleWad, WAD);
      if (w.shares[i] != 0 && out.shares[i] == 0) revert SharesRoundedToZero();
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L256-261)
```text
  function _validatePositiveWeights(LiquidityDelta calldata d) internal pure {
    uint256 n = d.binIdxs.length;
    for (uint256 i; i < n; i++) {
      if (d.shares[i] == 0) revert ZeroWeight();
    }
  }
```
