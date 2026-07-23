### Title
Synthetic-ratio mid price in `AnchoredPriceProvider` has no ratio-level guard, allowing bad-price execution when one feed moves within its per-leg guard range — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider` supports a synthetic-ratio mode where the pool price is computed as `price(baseFeedId) / price(quoteFeedId)` (e.g. BTC/USD ÷ ETH/USD = BTC/ETH). Each individual feed leg is validated through `offchainOracle.priceGuard(feedId)`, but the resulting ratio is never checked against any guard. If one leg moves significantly within its own guard range — the direct analog of WBTC depegging from BTC while the BTC/USD feed remains valid — the synthetic mid is silently wrong, the reference band is centered on that wrong mid, and every swap in the pool executes at a bad price, draining LP funds.

---

### Finding Description

In `_getBidAndAskPrice()`, the two-leg read path is:

```solidity
// AnchoredPriceProvider.sol lines 258-272
(uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
if (!ok) return (0, type(uint128).max);

bytes32 _quote = quoteFeedId;
if (_quote != bytes32(0)) {
    (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
    if (!ok2 || mid2 == 0) return (0, type(uint128).max);
    mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);   // ← ratio, no guard
    spreadBps += spreadBps2;
}
return _computeBidAsk(mid, spreadBps);
``` [1](#0-0) 

`_readLeg` applies a per-feed guard:

```solidity
// AnchoredPriceProvider.sol lines 290-292
(uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
guardMax = guardMax == 0 ? type(uint128).max : guardMax;
if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);
``` [2](#0-1) 

After the ratio is computed, `_computeBidAsk` builds the reference band directly from the (wrong) `mid`:

```solidity
// AnchoredPriceProvider.sol lines 308-313
uint256 half = spreadBps * ONE_BPS_E18 + minMargin;
uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
    return (0, type(uint128).max);
}
``` [3](#0-2) 

The band clamp in `_computeBidAsk` only enforces a minimum spread width; it does not detect that the mid itself is wrong. There is no mechanism — no `priceGuard`, no `MAX_SPREAD_BPS` check, no velocity guard at this layer — that validates the ratio `mid1 / mid2` against an expected range.

---

### Impact Explanation

A pool whose `AnchoredPriceProvider` is configured in synthetic-ratio mode (e.g. BTC/ETH derived from BTC/USD ÷ ETH/USD) will execute every swap at the wrong price for as long as one leg is displaced within its per-leg guard. Traders can atomically drain the LP's underpriced token. Because the band is centered on the wrong mid, the spread fee does not compensate LPs — it is a fraction of the wrong mid, not of the true price. This is a direct loss of LP principal, matching the "bad-price execution" and "LP asset loss" impact categories.

---

### Likelihood Explanation

The trigger is an ordinary oracle price update — no privileged action, no malicious setup. Any significant move in one of the two underlying feeds (e.g. ETH/USD dropping from 3 000 to 1 000 while remaining inside a guard of [100, 10 000]) shifts the synthetic ratio by 3× while both per-leg guards pass. This can occur during market stress, a flash-crash, or a genuine depeg of one of the underlying assets — exactly the scenario described in the external report for WBTC/BTC.

---

### Recommendation

Add a configurable ratio guard to `AnchoredPriceProvider` for synthetic-ratio mode. After computing `mid = Math.mulDiv(mid1, ORACLE_DECIMALS, mid2)`, check it against an immutable or factory-set `[ratioGuardMin, ratioGuardMax]` range and return the stall sentinel `(0, type(uint128).max)` on violation. This is the direct analog of the "double oracle" recommendation in the external report: the per-leg guards protect individual feeds; the ratio guard protects the derived price that actually reaches the pool.

---

### Proof of Concept

**Setup**: Deploy `AnchoredPriceProvider` with `baseFeedId = BTC/USD`, `quoteFeedId = ETH/USD`, per-leg guards `BTC/USD ∈ [10 000e8, 200 000e8]`, `ETH/USD ∈ [100e8, 10 000e8]`, `minMargin = 0.1%`, `MAX_SPREAD_BPS = 200`.

**Normal state**:
- BTC/USD = 60 000e8, ETH/USD = 3 000e8 → synthetic BTC/ETH mid = 20e8
- Both per-leg guards pass; band ≈ 20 ± 0.1%

**Depeg event** (ETH/USD oracle reports 1 000e8 — within its guard [100e8, 10 000e8]):
- Both `_readLeg` calls return `ok = true`
- Synthetic mid = 60 000e8 / 1 000e8 × 1e8 = 60e8 (3× the true value)
- Band ≈ 60 ± 0.1% — centered on the wrong price
- Pool's `swap()` calls `_getBidAndAskPriceX64()`, which calls `getBidAndAskPrice()`, which returns bid/ask around 60 BTC/ETH
- An attacker sells BTC to the pool at ~60 ETH/BTC; true market rate is ~20 ETH/BTC
- Attacker receives 3× the ETH they are owed; LPs lose ~2/3 of their ETH balance
- No extension guard at the `AnchoredPriceProvider` layer halts the swap [1](#0-0) [4](#0-3) [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-272)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L299-349)
```text
    function _computeBidAsk(uint256 mid, uint256 spreadBps)
        internal view returns (uint128, uint128)
    {
        // Circuit breaker: extreme (combined) uncertainty means the feed is clearly broken.
        if (spreadBps > MAX_SPREAD_BPS) {
            return (0, type(uint128).max);
        }

        // Reference band: mid ± (spreadBps + minMargin), bid rounded down, ask rounded up.
        uint256 half = spreadBps * ONE_BPS_E18 + minMargin; // < BPS_BASE_U by construction (spreadBps <= MAX_SPREAD_BPS here)
        uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
        uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
        if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
            return (0, type(uint128).max);
        }

        // Custom quote: source (both variants) or shaped reference quote (customizable variant).
        //    Immutable reference mode quotes the band directly — zero knob SLOADs.
        address _source = source;
        uint256 cBid;
        uint256 cAsk;
        if (_source != address(0)) {
            // 7a. Source mode: any failure (revert, OOG, garbage, zero, inverted) halts — fail
            //     closed. Knobs do NOT post-process the source output (the source shapes itself).
            bool ok;
            (ok, cBid, cAsk) = _readSource(_source);
            if (!ok) {
                return (0, type(uint128).max);
            }
        } else if (MUTABLE_PARAMS) {
            // 7b. Shaped reference quote: mid ± mid·spreadBps·confidence, then the marginStep step
            //     factors — PriceProvider semantics, clamped into the band below.
            bool ok;
            (ok, cBid, cAsk) = _shapedQuote(mid, spreadBps);
            if (!ok) {
                return (0, type(uint128).max);
            }
        } else {
            return (uint128(refBid), uint128(refAsk));
        }

        // 8. Clamp: out-of-band custom quotes are clipped silently to the band edge.
        //    bid ≤ refBid < refAsk ≤ ask, so bid < ask holds by construction.
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
        if (bidOut == 0 || bidOut >= askOut) {
            return (0, type(uint128).max);
        }

        return (uint128(bidOut), uint128(askOut));
    }
```
