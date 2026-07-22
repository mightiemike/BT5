### Title
`PriceVelocityGuardExtension` Velocity Check Unconditionally Bypassed on First Swap Due to Zero-Initialized `lastMidPriceX64` — (`File: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap()` skips its entire velocity enforcement block when `prevMid == 0`. Because `lastMidPriceX64` is zero-initialized in storage, the very first swap on any pool that uses this extension bypasses the guard unconditionally. An unprivileged caller can supply an arbitrarily manipulated `bidPriceX64`/`askPriceX64` pair on that first swap, executing at a bad price that the guard was specifically deployed to prevent, and simultaneously anchoring the stored baseline to the manipulated value.

---

### Finding Description

`PriceVelocityGuardExtension.beforeSwap()` reads the stored `lastMidPriceX64` into `prevMid`, writes the new mid price back to storage, and then gates the entire velocity check behind `if (prevMid != 0)`:

```solidity
// PriceVelocityGuardExtension.sol lines 53-76
PriceVelocityState storage s = priceVelocityState[pool_];
uint128 prevMid = s.lastMidPriceX64;
uint64 prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64 = midPrice;          // state written BEFORE the check
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {                    // guard skipped when prevMid == 0
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        // velocity math and revert
    }
}
``` [1](#0-0) 

`lastMidPriceX64` is a `uint128` field in a `mapping(address pool => PriceVelocityState)`. It is never set during pool creation or extension initialization; it starts at zero for every newly deployed pool. Therefore `prevMid == 0` is always true for the first swap, and the velocity check is always skipped.

The pool's `swap()` function accepts `bidPriceX64` and `askPriceX64` as caller-supplied parameters. The only in-pool validation is `bid < ask` and `bid != 0`. The velocity guard is the sole mechanism that bounds how far those caller-supplied prices may deviate from the previously observed market mid. With the guard bypassed, the caller may supply any valid `(bid, ask)` pair — including one that is orders of magnitude away from the true oracle price — and the pool will execute the swap at that price. [2](#0-1) 

After the first swap completes, `lastMidPriceX64` is permanently set to the manipulated mid price. All subsequent velocity checks are then anchored to that corrupted baseline, so the attacker's manipulated price becomes the new "normal" from which future allowed deviations are measured.

---

### Impact Explanation

- **Bad-price execution**: The pool settles the swap using the caller-supplied `bidPriceX64`/`askPriceX64` via `SwapMath`. If those prices are far from the true oracle mid, the trader receives more token output than the oracle curve permits, or pays less input than owed, directly extracting value from LP reserves.
- **Corrupted velocity baseline**: After the bypass, `lastMidPriceX64` is set to the manipulated value. The guard now permits future price moves relative to the attacker-chosen anchor, permanently weakening the protection for the pool's lifetime unless an admin calls `setLastMidPrice` to reset it.
- **LP principal loss**: LPs bear the cost of the mispriced swap. This is a direct loss of user principal above Sherlock thresholds for any pool with meaningful liquidity.

---

### Likelihood Explanation

- **Trigger is unprivileged**: Any address can call `MetricOmmPool.swap()`. No special role or allowlist membership is required to be the first swapper.
- **Deterministic**: The bypass occurs on every newly deployed pool that uses this extension. There is no race condition or probabilistic element; the attacker simply needs to be the first to call `swap()`.
- **No admin action required**: The attacker does not need to manipulate any admin-controlled parameter. The zero initial state is structural.
- **Timing**: The window is open from pool deployment until the first swap. On a freshly deployed pool with liquidity already added (a normal deployment sequence), this window is immediately exploitable.

---

### Recommendation

Initialize `lastMidPriceX64` to a sentinel that forces the guard to run on the first swap, or require the admin to seed the price before any swap is permitted. Two concrete options:

1. **Require seeding before first swap**: Add a check that reverts if `prevMid == 0` and `maxChange != 0`, forcing the admin to call `setLastMidPrice` before the pool opens for trading.

2. **Seed during `initialize`**: Add an `initialize` override (called by the factory at pool creation) that accepts an initial mid price and sets `lastMidPriceX64` and `lastUpdateBlock` atomically, so the guard is active from the very first swap.

Either approach closes the zero-state bypass and preserves the invariant that no swap can move the price faster than the configured velocity envelope.

---

### Proof of Concept

```
Setup:
  1. Deploy MetricOmmPool with PriceVelocityGuardExtension configured.
  2. Admin calls setMaxChangePerBlock(pool, 1e15)  // 0.1% per block — tight guard.
  3. Admin does NOT call setLastMidPrice (or calls it with 0).
  4. LP adds liquidity to the pool.

Attack (first swap, block N):
  5. Attacker calls pool.swap(
         recipient = attacker,
         zeroForOne = true,
         amountSpecified = large,
         priceLimitX64 = 0,
         bidPriceX64 = 1,          // absurdly low bid
         askPriceX64 = 2,          // absurdly low ask  (bid < ask, bid != 0 ✓)
         extensionData = ""
     )

Trace through PriceVelocityGuardExtension.beforeSwap():
  - midPrice = SwapMath.midAndSpreadFeeX64FromBidAsk(1, 2) → ~1
  - prevMid  = priceVelocityState[pool].lastMidPriceX64 == 0
  - s.lastMidPriceX64 = 1   (written)
  - s.lastUpdateBlock  = N  (written)
  - if (prevMid != 0) → FALSE → velocity check SKIPPED
  - returns beforeSwap.selector  ← guard passes

Result:
  - Pool executes swap at bid=1/ask=2 (far below true oracle price).
  - Attacker receives token1 at a fraction of its true value.
  - LP reserves are drained by the price difference.
  - lastMidPriceX64 is now anchored to 1, corrupting future velocity checks.
``` [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-79)
```text
  function beforeSwap(
    address,
    address,
    bool,
    int128,
    uint128,
    uint256,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata
  ) external override returns (bytes4) {
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);

    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
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
    }

    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
