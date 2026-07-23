### Title
`PriceVelocityGuardExtension` Permanently Freezes All Swaps During Rapid Legitimate Price Moves — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` is a direction-agnostic guard that reverts every swap when the oracle mid-price has moved faster than `maxChangePerBlockE18` since the last recorded block. Because the storage write to `lastMidPriceX64` is performed **before** the revert check, a failed transaction rolls back the write, leaving the stored reference price permanently anchored at the pre-crash value. Every subsequent swap attempt reads the same stale reference, computes the same excess velocity, and reverts again. The pool is completely frozen for trading in both directions until the pool admin manually calls `setLastMidPrice` — an exact structural analog to the M-05 anti-flash-loan deadlock.

---

### Finding Description

`beforeSwap` in `PriceVelocityGuardExtension` executes in this order:

```solidity
// lines 57-58: storage written FIRST
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
            revert PriceVelocityExceeded(actualSq, allowedSq);  // line 73
        }
    }
}
```

When the revert fires at line 73, the EVM rolls back the writes at lines 57–58. `lastMidPriceX64` is restored to `prevMid`. On the next swap attempt the oracle still returns the same new price, the same delta is computed, and the same revert fires. This cycle repeats indefinitely.

The guard is also **direction-agnostic**: it does not distinguish between a swap that would harm the pool and one that would benefit it. A genuine market crash that moves the oracle price 15% in one block will block both the sell direction and the buy direction equally.

The only escape is a privileged call to `setLastMidPrice`, which requires the pool admin to act before any swap can proceed.

---

### Impact Explanation

All swaps through the pool are completely blocked in both directions for the duration of the rapid price move. This satisfies the "broken core pool functionality causing loss of funds or unusable swap flows" impact gate:

- Traders cannot execute any swap, regardless of direction or size.
- Arbitrageurs cannot rebalance the pool against the new oracle price.
- If the pool also carries `OracleValueStopLossExtension`, that guard's `afterSwap` hook never fires (no swap reaches settlement), so the stop-loss protection is silently bypassed while the pool's bin balances continue to be exposed to the new oracle price on the next LP withdrawal.
- LPs who wish to exit via swap (rather than `removeLiquidity`) are blocked.

The deadlock persists for as long as the oracle price remains more than `maxChangePerBlockE18 * sqrt(1 + blockDiff)` away from the stored reference — which during a sustained crash can be many blocks.

---

### Likelihood Explanation

Any genuine rapid market move — a flash crash, a large liquidation cascade, or a high-volatility news event — that moves the oracle mid-price beyond the configured per-block cap in a single block triggers the deadlock. The trigger requires no privileged access and no special setup: any public swap call against a pool with this extension active is sufficient. On chains with fast block times (e.g., HyperEVM), even moderate sustained moves can exceed a tightly configured cap across consecutive blocks.

---

### Recommendation

Move the storage writes **after** the velocity check so that a failed check leaves `lastMidPriceX64` unchanged:

```solidity
// Check first, write only on success
if (prevMid != 0 && maxChange != 0) {
    // ... compute actualSq, allowedSq ...
    if (actualSq > allowedSq) revert PriceVelocityExceeded(actualSq, allowedSq);
}
s.lastMidPriceX64 = midPrice;
s.lastUpdateBlock = uint64(block.number);
```

Additionally, consider making the guard direction-aware (analogous to the stop-loss extension's `zeroForOne` branching) so that a rapid price move in one direction does not block swaps in the opposite direction that would restore the pool toward equilibrium.

---

### Proof of Concept

1. Deploy a pool with `PriceVelocityGuardExtension` configured with `maxChangePerBlockE18 = 0.05e18` (5% per block).
2. Execute one swap at oracle mid-price `P₀`. Extension writes `lastMidPriceX64 = P₀`, `lastUpdateBlock = N`.
3. In block `N+1`, the oracle reports mid-price `P₁ = 0.80 * P₀` (20% crash — legitimate market move).
4. Any swap attempt:
   - Reads `P₁` from oracle.
   - Writes `lastMidPriceX64 = P₁` (temporary).
   - Computes `changeE18 = 0.20e18`, `actualSq = 0.04e36`, `allowedSq = 0.05² * 2 * e36 = 0.005e36`.
   - `0.04e36 > 0.005e36` → `revert PriceVelocityExceeded`.
   - Storage rolls back; `lastMidPriceX64` stays at `P₀`.
5. Repeat step 4 in block `N+2`, `N+3`, … — every attempt reverts identically.
6. Pool is frozen. Only `setLastMidPrice(pool, P₁)` by the pool admin unblocks it. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-34)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-74)
```text
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
```

**File:** metric-periphery/contracts/interfaces/extensions/IPriceVelocityGuardExtension.sol (L7-11)
```text
  struct PriceVelocityState {
    uint128 lastMidPriceX64;
    uint64 lastUpdateBlock;
    uint64 maxChangePerBlockE18;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```
