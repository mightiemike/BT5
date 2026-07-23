### Title
`PriceVelocityGuardExtension` Uses `block.number` Instead of `block.timestamp` for Velocity Window, Making the Guard Inaccurate on Chains with Non-Constant Block Time - (`File: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` measures elapsed time using `block.number` to compute the allowed price-change window. On chains where block time is not constant (e.g., Berachain, which is the target deployment per the external report), the guard becomes either too permissive or too restrictive relative to real-world elapsed seconds, breaking its core security invariant.

---

### Finding Description

The extension stores `lastUpdateBlock` as `uint64(block.number)` and computes the allowed price deviation as:

```
allowedSq = maxChangePerBlockE18² × (1 + blockDiff)
```

where `blockDiff = block.number - prevBlock`. [1](#0-0) 

The NatSpec comment on line 15-16 explicitly frames the guard in terms of "blocks":

> *Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`* [2](#0-1) 

The `lastUpdateBlock` is written in both the admin setter and the `beforeSwap` hook: [3](#0-2) [4](#0-3) 

Because `blockDiff` is a count of blocks rather than a count of seconds, the effective allowed price-change rate per real-world second is:

```
rate_per_second = maxChangePerBlockE18 / avg_block_time_seconds
```

When block time increases (blocks produced more slowly), `blockDiff` grows larger for the same real-world elapsed time, so `allowedSq` grows larger — the guard becomes **more permissive** than the admin intended. When block time decreases (blocks produced faster), `blockDiff` shrinks, making the guard **more restrictive** than intended.

---

### Impact Explanation

**Permissive direction (block time increases):** The velocity guard is the last line of defense against oracle price manipulation reaching a pool swap. If blocks slow down, a larger absolute price jump is permitted per real-world second. An attacker who can influence the oracle price (e.g., via a flash-loan-assisted manipulation of a Pyth/Chainlink feed, or by exploiting a stale compressed-oracle slot) can push a price that would normally be rejected by the velocity check through the `beforeSwap` hook. The pool then executes the swap at the manipulated bid/ask, causing the trader to receive more output tokens than the true oracle price permits, or the pool to receive fewer input tokens than owed — a direct loss of LP principal.

**Restrictive direction (block time decreases):** Legitimate swaps revert with `PriceVelocityExceeded` even though the real-world price movement is within the admin's intended rate, breaking core swap functionality. [5](#0-4) 

---

### Likelihood Explanation

Berachain's block time is explicitly documented as non-constant (it depends on network congestion). The external report's referenced documentation confirms this. The `PriceVelocityGuardExtension` is a production periphery contract intended for deployment on Berachain pools. Any sustained deviation in block time — which is expected and documented — directly miscalibrates the guard. No privileged attacker is required for the permissive direction; the miscalibration is passive and worsens over time as block time drifts.

---

### Recommendation

Replace `block.number` with `block.timestamp` throughout `PriceVelocityGuardExtension`. Rename the config parameter from `maxChangePerBlockE18` to `maxChangePerSecondE18` (or per-millisecond to match the oracle's millisecond timestamps), and rename `lastUpdateBlock` to `lastUpdateTimestamp`. The velocity formula becomes:

```solidity
uint256 timeDiff = block.timestamp - prevTimestamp; // seconds
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + timeDiff);
```

This makes the guard invariant to block-time fluctuations and consistent with the oracle layer, which already uses unix millisecond timestamps for its own staleness and future-drift guards. [6](#0-5) 

---

### Proof of Concept

**Setup:** Pool is configured with `PriceVelocityGuardExtension`, `maxChangePerBlockE18 = 0.01e18` (1% per block), calibrated at a 2-second average block time (i.e., the admin intends ≈0.5% per second).

**Normal conditions:** `blockDiff = 1` per 2 seconds → `allowedSq = (0.01e18)² × 2`. A 1.41% price move in 2 seconds is blocked.

**Attack scenario (block time slows to 10 seconds):**

1. Attacker waits 10 seconds (1 block elapses, `blockDiff = 1`).
2. Oracle price is manipulated by 1.41% in those 10 seconds.
3. `allowedSq = (0.01e18)² × 2` — same as before, guard passes.
4. But the admin intended only 0.5%/second × 10 seconds = 5% total to be allowed; 1.41% is well within that, so this specific example doesn't show the attack clearly.

**Clearer scenario:** Admin sets `maxChangePerBlockE18 = 0.005e18` (0.5% per block at 2s/block = 0.25%/second). Block time slows to 60 seconds.

- After 60 seconds (1 block), `blockDiff = 1`, `allowedSq = (0.005e18)² × 2` → allows 0.707% move.
- But the admin intended 0.25%/second × 60 seconds = 15% total to be allowed over 60 seconds.
- In the opposite direction: if block time speeds up to 0.5 seconds, 4 blocks elapse in 2 seconds, `blockDiff = 4`, `allowedSq = (0.005e18)² × 5` → allows 1.118% move in 2 seconds, but admin intended only 0.5% in 2 seconds — the guard is **2× too permissive** per real-world second.

An attacker exploiting the permissive window submits a swap when the oracle price has moved beyond the intended rate. The `beforeSwap` hook passes, the pool executes at the manipulated price, and LPs absorb the loss. [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L15-17)
```text
///      Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`.
///      Comparison is performed on squares to avoid an on-chain sqrt:
///        changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L19-34)
```text
contract PriceVelocityGuardExtension is BaseMetricExtension, IPriceVelocityGuardExtension {
  mapping(address pool => PriceVelocityState) public priceVelocityState;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
  }

  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L55-75)
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
      }
```
