Based on my investigation of the codebase, I can identify a structural analog in `AnchoredPriceProvider.sol`.

---

### Title
Single `MAX_REF_STALENESS` Applied to Both Legs of Synthetic Quote Allows Stale-Price Execution — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

### Summary
`AnchoredPriceProvider` supports a two-feed synthetic ratio mode (e.g., BTC/USD ÷ ETH/USD = BTC/ETH). Both legs are validated against the same immutable `MAX_REF_STALENESS`. When the two Chainlink feeds have different heartbeats, any single staleness value either silently accepts a stale price from the faster-updating feed or permanently DoS-es the slower-updating feed — the exact structural analog to the external report.

### Finding Description

`AnchoredPriceProvider` stores one staleness bound for the entire provider: [1](#0-0) 

The constructor enforces only an upper bound of 7 days and allows zero (same-block requirement), but imposes no per-feed granularity: [2](#0-1) 

`_readLeg` applies this single bound to whichever `feedId` it is called with: [3](#0-2) 

In synthetic mode, `_getBidAndAskPrice` calls `_readLeg` for **both** `baseFeedId` and `quoteFeedId` through the same bound: [4](#0-3) 

Consider a USDC/ETH synthetic provider:
- `baseFeedId` = USDC/USD — Chainlink heartbeat **86 400 s**
- `quoteFeedId` = ETH/USD — Chainlink heartbeat **3 600 s**

**Case A — `MAX_REF_STALENESS = 86 400 s`** (set to accommodate USDC/USD):  
ETH/USD can be up to 23 hours and 59 minutes stale and still pass `_isStale`. The synthetic ratio `USDC/USD ÷ ETH/USD` is computed with a massively stale ETH denominator, producing a wildly wrong bid/ask that is then clamped and forwarded to the pool swap.

**Case B — `MAX_REF_STALENESS = 3 600 s`** (set to accommodate ETH/USD):  
USDC/USD, which legitimately updates every 24 hours, is rejected as stale on every call between heartbeats, permanently bricking the provider and any pool that depends on it.

Neither case is a misconfiguration that can be corrected post-deployment: `MAX_REF_STALENESS` is **immutable**. [5](#0-4) 

### Impact Explanation
In Case A, a trader observing that ETH/USD has not been updated for several hours can swap against the pool at a price that no longer reflects the true ETH value. The pool's LPs absorb the loss. This is a direct loss of LP principal — matching the "bad-price execution: stale bid/ask reaches a pool swap" allowed impact.

### Likelihood Explanation
Synthetic providers pairing a stablecoin feed (86 400 s heartbeat) with a volatile-asset feed (3 600 s heartbeat) are a natural and documented use case (the contract comment explicitly names BTC/USD ÷ ETH/USD as an example). Any such deployment is structurally forced into one of the two failure modes above. No privileged access or malicious setup is required; normal swap traffic triggers the bad-price path.

### Recommendation
Replace the single `MAX_REF_STALENESS` immutable with two per-leg immutables:

```solidity
uint256 public immutable MAX_BASE_STALENESS;
uint256 public immutable MAX_QUOTE_STALENESS;
```

Pass both through the constructor and apply each only to its respective `_readLeg` call:

```solidity
// base leg
if (_isStale(refTime, block.timestamp, MAX_BASE_STALENESS)) ...

// quote leg
if (_isStale(refTime, block.timestamp, MAX_QUOTE_STALENESS)) ...
```

This mirrors the per-asset staleness design recommended in the external report and eliminates the structural conflict between feeds with different heartbeats.

### Proof of Concept

1. Deploy `AnchoredPriceProvider` with:
   - `baseFeedId` = USDC/USD (Chainlink heartbeat 86 400 s)
   - `quoteFeedId` = ETH/USD (Chainlink heartbeat 3 600 s)
   - `MAX_REF_STALENESS = 86400` (chosen to avoid DoS-ing the USDC feed)
2. ETH/USD is last updated at `T`. At `T + 82 800` (23 hours later) ETH has moved 5%.
3. A trader calls `getBidAndAskPrice()` through the pool's swap path.
4. `_readLeg(baseFeedId)` passes — USDC/USD is fresh.
5. `_readLeg(quoteFeedId)` passes — `(now - T) = 82 800 < 86 400 = MAX_REF_STALENESS`.
6. `mid = mulDiv(USDC_mid, ORACLE_DECIMALS, ETH_mid_23h_stale)` — the ratio uses the stale ETH price.
7. The resulting bid/ask is ~5% off from the true market price.
8. The trader swaps, extracting value from LPs at the stale price. [6](#0-5)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L64-81)
```text
    // ── Immutables ──────────────────────────────────────────────────────
    IOffchainOracle public immutable offchainOracle;
    bytes32         public immutable baseFeedId;
    /// @notice Optional second feed for synthetic ratio quoting; zero = single-feed (no conversion).
    ///         Synthetic mid = price(baseFeedId) / price(quoteFeedId), e.g. BTC/USD ÷ ETH/USD = BTC/ETH.
    bytes32         public immutable quoteFeedId;
    /// @dev anchor factory (governs setSource), NOT the AMM pool factory passed at read.
    address         public immutable factory;

    /// @notice Per-side minimum margin on top of the reference spread, BPS_BASE_U scale (1 bps = 1e14).
    uint256 public immutable minMargin;
    /// @notice Reference older than this (seconds) halts quoting — never clamp to a stale anchor.
    ///         Zero means the reference must be in the current block (refTime == block.timestamp).
    uint256 public immutable MAX_REF_STALENESS;
    /// @notice Circuit breaker: reference uncertainty above this (bps) means the feed is broken — halt.
    ///         Below it, growing `spreadBps` only widens the band (widen, don't halt).
    uint16  public immutable MAX_SPREAD_BPS;

```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L150-151)
```text
        if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds(); // 0 allowed = same-block reference
        MAX_REF_STALENESS = _maxRefStaleness;
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-271)
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
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-283)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
