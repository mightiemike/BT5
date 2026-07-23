### Title
Single `MAX_REF_STALENESS` Applied to Both Feeds in Synthetic Mode Allows Stale Quote-Leg Price to Reach Pool Swaps - (File: `smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider` supports a two-feed synthetic mode (`quoteFeedId != 0`) that computes a ratio price (e.g., BTC/USD ÷ ETH/USD = BTC/ETH). Both the base feed and the quote feed are validated for staleness through the same `_readLeg` helper, which applies the single immutable `MAX_REF_STALENESS` to both. Because the factory envelope that bounds `MAX_REF_STALENESS` is keyed exclusively on `baseFeedId`, the quote feed's own heartbeat interval is never independently constrained. A deployer who sets `MAX_REF_STALENESS` to match the base feed's heartbeat (e.g., 24 h for BTC/USD) leaves the quote feed (e.g., ETH/USD, 1 h heartbeat) able to be up to 23 h stale while still passing the staleness guard, causing a materially wrong synthetic bid/ask to reach pool swaps.

---

### Finding Description

`AnchoredPriceProvider._getBidAndAskPrice()` calls `_readLeg` for both feeds:

```solidity
// AnchoredPriceProvider.sol lines 258–271
function _getBidAndAskPrice() internal returns (uint128, uint128) {
    (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
    if (!ok) return (0, type(uint128).max);

    bytes32 _quote = quoteFeedId;
    if (_quote != bytes32(0)) {
        (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
        if (!ok2 || mid2 == 0) return (0, type(uint128).max);
        mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
        spreadBps += spreadBps2;
    }
    return _computeBidAsk(mid, spreadBps);
}
``` [1](#0-0) 

Inside `_readLeg`, the staleness check uses the single `MAX_REF_STALENESS` immutable regardless of which feed is being read:

```solidity
// AnchoredPriceProvider.sol lines 277–295
function _readLeg(bytes32 feedId)
    internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
{
    (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
    if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
    ...
}
``` [2](#0-1) 

`MAX_REF_STALENESS` is a single immutable set at construction time, bounded only by the factory envelope for `baseFeedId`:

```solidity
// AnchoredPriceProvider.sol line 77
uint256 public immutable MAX_REF_STALENESS;
``` [3](#0-2) 

The factory's `createAnchoredProvider` validates `maxRefStaleness` against the envelope, but the envelope is keyed **only on `baseFeedId`**:

```solidity
// AnchoredProviderFactory.sol lines 171–172
bytes32 classId = feedClass[baseFeedId];
if (classId == bytes32(0)) classId = DEFAULT_CLASS;
``` [4](#0-3) 

The factory's own NatSpec confirms this: *"The envelope is keyed on `baseFeedId` (the provider's class); the ref feed only contributes its uncertainty and is validated for existence at provider construction."* [5](#0-4) 

There is no per-feed staleness bound for `quoteFeedId` anywhere in the factory or provider. The deployer is structurally unable to set a single `MAX_REF_STALENESS` that is simultaneously correct for two feeds with different heartbeat intervals.

---

### Impact Explanation

When `MAX_REF_STALENESS` is set to match the base feed's heartbeat (the only envelope-validated choice), the quote feed can be stale by up to `MAX_REF_STALENESS − 1 second` while still passing `_readLeg`. The synthetic mid is computed as:

```
mid = price(baseFeedId) / price(quoteFeedId)
```

A stale `price(quoteFeedId)` directly corrupts the synthetic ratio. For example, if ETH/USD is 23 h stale and ETH has dropped 20 % in that window, the synthetic BTC/ETH price is 25 % too high. The resulting bid/ask passed to the pool is materially wrong. Traders can swap against the inflated ask (buying BTC at a discount) or the deflated bid (selling BTC at a premium), draining LP principal. This is a direct bad-price execution impact on LP funds.

---

### Likelihood Explanation

The synthetic two-feed mode is explicitly documented and supported by the factory (`quoteFeedId` parameter in `createAnchoredProvider`). The factory comment gives BTC/USD ÷ ETH/USD = BTC/ETH as the canonical example — two feeds with different heartbeat intervals (BTC/USD: 24 h, ETH/USD: 1 h on most networks). Any deployer who sets `MAX_REF_STALENESS` to accommodate the slower feed (the rational choice to avoid DoS) leaves the faster feed's staleness window wide open. The trigger is a normal oracle delay or network hiccup on the faster feed — no adversarial action is required to create the stale state; a trader only needs to observe it and execute a swap.

---

### Recommendation

Introduce a per-leg staleness threshold. The simplest fix is to add a `maxQuoteRefStaleness` immutable alongside `MAX_REF_STALENESS` and pass the appropriate threshold to `_readLeg`:

```solidity
uint256 public immutable MAX_REF_STALENESS;       // for baseFeedId
uint256 public immutable MAX_QUOTE_REF_STALENESS; // for quoteFeedId (0 = same as base)

function _getBidAndAskPrice() internal returns (uint128, uint128) {
    (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId, MAX_REF_STALENESS);
    if (!ok) return (0, type(uint128).max);

    bytes32 _quote = quoteFeedId;
    if (_quote != bytes32(0)) {
        uint256 quoteStaleness = MAX_QUOTE_REF_STALENESS == 0
            ? MAX_REF_STALENESS
            : MAX_QUOTE_REF_STALENESS;
        (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote, quoteStaleness);
        ...
    }
}
```

The factory envelope should be extended with `stalenessQuoteMin`/`stalenessQuoteMax` fields keyed on `quoteFeedId`'s class, and `createAnchoredProvider` should validate both.

---

### Proof of Concept

**Setup:**
- Deploy `AnchoredPriceProvider` with `baseFeedId` = BTC/USD (24 h heartbeat), `quoteFeedId` = ETH/USD (1 h heartbeat), `MAX_REF_STALENESS` = 24 h (within the BTC/USD envelope).
- Register a pool against this provider.

**Attack:**
1. ETH/USD feed is not pushed for 23 h (oracle delay or network issue). Stored `refTime` for ETH/USD is 23 h old. ETH price has fallen 20 % in that window.
2. BTC/USD feed is current (pushed within the last hour).
3. Attacker calls `swap(zeroForOne=true)` on the pool.
4. Pool calls `getBidAndAskPrice()` → `_getBidAndAskPrice()` → `_readLeg(quoteFeedId)`.
5. `_isStale(refTime, block.timestamp, 24h)` → `23h < 24h` → **not stale** → passes.
6. Synthetic mid = `BTC_USD_current / ETH_USD_23h_old` ≈ 25 % above true BTC/ETH.
7. The inflated ask is passed to the pool. Attacker buys BTC at ~25 % below true market price.
8. LP principal is drained by the difference between the stale synthetic price and the true market price. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L75-78)
```text
    /// @notice Reference older than this (seconds) halts quoting — never clamp to a stale anchor.
    ///         Zero means the reference must be in the current block (refTime == block.timestamp).
    uint256 public immutable MAX_REF_STALENESS;
    /// @notice Circuit breaker: reference uncertainty above this (bps) means the feed is broken — halt.
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L222-230)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta
    ) internal pure returns (bool) {
        if (refTime == 0) return true;
        if (refTime > nowTs) return true;
        return (nowTs - refTime) > maxDelta;
    }
```

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

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L153-155)
```text
    /// @param quoteFeedId optional second feed for synthetic ratio quoting (zero = single-feed). The
    ///        envelope is keyed on `baseFeedId` (the provider's class); the ref feed only contributes its
    ///        uncertainty and is validated for existence at provider construction.
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L171-172)
```text
        bytes32 classId = feedClass[baseFeedId];
        if (classId == bytes32(0)) classId = DEFAULT_CLASS;
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L176-180)
```text
        if (
            minMargin < env.minMarginMin || minMargin > env.minMarginMax
            || maxRefStaleness < env.stalenessMin || maxRefStaleness > env.stalenessMax
            || maxSpreadBps < env.maxSpreadMin || maxSpreadBps > env.maxSpreadMax
        ) revert ParamsOutOfEnvelope();
```
