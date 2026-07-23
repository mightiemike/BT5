### Title
`PriceVelocityGuardExtension` Uses `block.number` for Velocity Accounting, Making the Guard Systematically Too Permissive on Fast-Block Chains — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` measures price velocity in **blocks**, not seconds. On any chain whose block time is shorter than Ethereum's ~12 s (e.g., Avalanche ~1 s, BSC ~3 s), the same `maxChangePerBlockE18` value permits proportionally more price movement per unit of wall-clock time, weakening or completely defeating the guard's intended protection for LP funds.

---

### Finding Description

The extension stores the block number of the last observed mid-price and computes the allowed deviation as:

```
allowedSq = maxChange² × (1 + blockDiff)
```

where `blockDiff = block.number − prevBlock`. [1](#0-0) 

The NatDoc explicitly frames the parameter as a **per-block** rate:

> *"Caps how fast the provided price can move between blocks, per pool."*
> *"Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`."* [2](#0-1) 

`setLastMidPrice` also anchors the baseline to `block.number`: [3](#0-2) 

**Chain-time mismatch.** Suppose a pool admin calibrates `maxChangePerBlockE18` for Ethereum (12 s/block) to allow at most P% price movement per 12 seconds. On Avalanche (1 s/block), 12 real seconds correspond to 12 blocks, so `blockDiff = 12` and the guard allows `P × sqrt(13)` ≈ 3.6 × P% movement in the same 12 seconds — a 3.6× loosening of the cap. On a 1 s/block chain the guard degenerates to roughly `sqrt(N)` times more permissive than intended for any N-second window.

The formula is hardcoded to `block.number`; there is no code path that lets the admin express the cap in seconds. The only mitigation available to the admin is to manually scale `maxChangePerBlockE18` down by the block-time ratio — a fragile, undocumented, off-chain requirement that is not enforced anywhere in the contract.

---

### Impact Explanation

The velocity guard is the `beforeSwap` hook that is supposed to prevent a manipulated oracle price from reaching the pool. If the guard is too permissive, a price feed that has moved far beyond the intended cap in real time is accepted, and the pool executes swaps at a bad price. LPs bear the resulting loss: the pool's bin balances are drained at an off-market rate, reducing the value per share below the high-watermark that the `OracleValueStopLossExtension` is separately tracking. This is a direct loss of LP principal — a **bad-price execution** impact.

---

### Likelihood Explanation

The protocol's own documentation and the `RESEARCHER.md` confirm intent to deploy on multiple chains. Any deployment on a chain with block time < 12 s (Avalanche, BSC, Polygon, Optimism, Arbitrum, Base, etc.) with a `maxChangePerBlockE18` value calibrated for Ethereum immediately exhibits the miscalibration. No attacker action is required to create the condition; the guard is simply weaker from the first swap. An attacker who observes the loosened cap can then time oracle updates to move the price by the larger-than-intended amount within a single block window and execute a profitable swap against the pool.

---

### Recommendation

Replace `block.number` with `block.timestamp` throughout `PriceVelocityGuardExtension`. Rename the parameter to `maxChangePerSecondE18` (or similar) and restate the invariant as:

```
allowedSq = maxChange² × (1 + timeDiff)   // timeDiff in seconds
```

This makes the guard chain-agnostic and consistent with the rest of the codebase, which already uses `block.timestamp` for all time-sensitive accounting (decay, timelocks, staleness checks in `OracleValueStopLossExtension`, `PriceProvider`, `AnchoredPriceProvider`, etc.). [4](#0-3) [5](#0-4) 

---

### Proof of Concept

**Setup:** Deploy `PriceVelocityGuardExtension` on Avalanche (1 s/block). Pool admin sets `maxChangePerBlockE18 = 1e16` (1% per block), intending to cap price movement at 1% per 12 seconds (Ethereum semantics).

**Attack:**

1. At block N, the oracle mid-price is recorded as `prevMid`. `prevBlock = N`.
2. Attacker waits 12 real seconds (= 12 Avalanche blocks). `blockDiff = 12`.
3. Attacker pushes a new oracle price that is `P%` higher, where `P = maxChange × sqrt(13) ≈ 3.6%`.
4. In `beforeSwap`:
   - `actualSq = (0.036)² × (1e18)² = 1.296e33`
   - `allowedSq = (1e16)² × 13 = 1.3e33`
   - `actualSq ≤ allowedSq` → **guard passes**.
5. On Ethereum the same 12-second, 3.6% move would span only 1 block (`blockDiff = 1`), giving `allowedSq = (1e16)² × 2 = 2e32 < 1.296e33` → **guard would revert**.

The pool executes the swap at the manipulated price; LPs receive less than fair value for the tokens they provide. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L9-18)
```text
/// @title PriceVelocityGuardExtension
/// @notice Caps how fast the provided price can move between blocks, per pool.
/// @dev This extension allows the pool admin to increase security of the pool by limiting price
///      manipulation through velocity constraints. However, it assumes that the pool admin is not
///      an adversary and acts to optimize pool profitability. The pool admin must be trusted.
///
///      Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`.
///      Comparison is performed on squares to avoid an on-chain sqrt:
///        changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
///      where 1e18 = 100% (full unit).
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-34)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L55-74)
```text
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
        }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L268-284)
```text
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }

    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L125-133)
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
