### Title
`PriceVelocityGuardExtension.setMaxChangePerBlock()` leaves stale `lastMidPriceX64`, permanently bricking pool swaps after guard activation — (`File: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`setMaxChangePerBlock` updates only `maxChangePerBlockE18` and leaves `lastMidPriceX64` / `lastUpdateBlock` untouched. When the guard is enabled (or re-enabled with a tighter cap) after the oracle price has drifted from the last recorded mid, every subsequent `beforeSwap` call reverts with `PriceVelocityExceeded`. Because the state write is inside the same transaction that reverts, `lastMidPriceX64` is never advanced, and the pool's swap path is permanently blocked until the admin separately calls `setLastMidPrice`.

---

### Finding Description

`PriceVelocityGuardExtension` stores per-pool velocity state in `priceVelocityState[pool]`:

```
struct PriceVelocityState {
    uint128 lastMidPriceX64;   // last recorded oracle mid
    uint64  lastUpdateBlock;
    uint64  maxChangePerBlockE18;
}
```

The admin setter only touches one field:

```solidity
// PriceVelocityGuardExtension.sol L24-27
function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
}
```

`lastMidPriceX64` and `lastUpdateBlock` are **not reset**.

`beforeSwap` then executes:

```solidity
// PriceVelocityGuardExtension.sol L53-76
PriceVelocityState storage s = priceVelocityState[pool_];
uint128 prevMid = s.lastMidPriceX64;
uint64  prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64 = midPrice;          // written BEFORE the check
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;
        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);
        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);
        uint256 actualSq  = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
        if (actualSq > allowedSq) {
            revert PriceVelocityExceeded(actualSq, allowedSq);   // ← whole tx reverts
        }
    }
}
```

The state writes on lines 57–58 are **inside the reverting transaction**. When `PriceVelocityExceeded` fires, the EVM unwinds them, so `lastMidPriceX64` snaps back to the stale value. The next swap reads the same stale `prevMid`, computes the same large delta, and reverts again — indefinitely.

**Concrete trigger sequence:**

1. Pool is deployed; guard starts with `maxChangePerBlockE18 = 0` (disabled). Swaps run normally; `lastMidPriceX64` is updated to value **X** (e.g. representing a mid of 1 000).
2. The oracle price drifts to **Y** (e.g. 1 500, a 50 % move) while the guard is still off.
3. Admin calls `setMaxChangePerBlock(pool, 1e16)` (1 % per-block cap) to harden the pool.
4. First swap: `prevMid = X = 1000`, `midPrice = Y = 1500`.
   - `changeE18 = 5 × 10^17` (50 %)
   - `actualSq ≈ 2.5 × 10^35`
   - `allowedSq = (10^16)^2 × (1 + blockDiff) ≈ 10^32` for any reasonable `blockDiff`
   - `actualSq > allowedSq` → **revert**; state rolls back; `lastMidPriceX64` stays at X.
5. Every subsequent swap repeats step 4 and reverts. The pool's swap path is **permanently bricked**.

The only recovery path is for the admin to call `setLastMidPrice` — a function that exists but is not documented as a required companion to `setMaxChangePerBlock`, and whose omission is not surfaced by any on-chain guard.

---

### Impact Explanation

The pool's `swap` function is permanently unusable after a routine admin configuration change. Traders cannot execute any swap; the pool's primary function is broken. This matches the allowed impact gate: **broken core pool functionality causing an unusable swap flow**. LP principal is not directly drained, but the pool is economically dead for trading until manual admin remediation.

---

### Likelihood Explanation

The trigger is a normal, good-faith admin action: enabling or tightening the velocity guard. Any pool that starts with the guard disabled (the zero-initialized default) and later activates it after the oracle price has moved — a common operational pattern — will hit this condition. No adversarial input is required; the bug fires on the first swap after `setMaxChangePerBlock`.

---

### Recommendation

Reset the velocity baseline atomically inside `setMaxChangePerBlock` so the guard starts from the current oracle price, not a stale one:

```solidity
function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    // Reset the baseline so the first post-activation swap is not compared
    // against a stale mid from a different price regime.
    s.lastMidPriceX64 = 0;
    s.lastUpdateBlock = uint64(block.number);
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
}
```

Setting `lastMidPriceX64 = 0` causes `beforeSwap` to skip the velocity check on the very first swap (the `if (prevMid != 0)` guard on line 60), record the live mid, and begin enforcing the cap from the next block onward — exactly the correct semantics for a fresh activation.

---

### Proof of Concept

```
State before:
  priceVelocityState[pool].maxChangePerBlockE18 = 0        // guard off
  priceVelocityState[pool].lastMidPriceX64      = 1000     // from last swap
  priceVelocityState[pool].lastUpdateBlock      = 100

Oracle price moves: mid = 1500 (50% above stored baseline)

Admin calls: setMaxChangePerBlock(pool, 1e16)   // 1% per-block cap
  → only maxChangePerBlockE18 updated; lastMidPriceX64 stays 1000

Block 200, swap arrives:
  prevMid   = 1000
  midPrice  = 1500
  blockDiff = 100
  delta     = 500
  changeE18 = 500 * 1e18 / 1000 = 5e17
  actualSq  = (5e17)^2 = 2.5e35
  allowedSq = (1e16)^2 * (1 + 100) = 1e32 * 101 ≈ 1.01e34
  actualSq > allowedSq → revert PriceVelocityExceeded
  (state rolled back; lastMidPriceX64 still 1000)

Block 201, next swap: identical computation → revert
Block 202, next swap: identical computation → revert
...
Pool swap path permanently bricked.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L24-27)
```text
  function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
  }
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-76)
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
      }
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
