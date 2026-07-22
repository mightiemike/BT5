### Title
PriceVelocityGuardExtension Velocity Check Silently Bypassed on First Swap Due to Uninitialized `lastMidPriceX64` — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` unconditionally skips the velocity check when `lastMidPriceX64 == 0`. Because the storage slot starts at zero and no initialization is enforced before swaps begin, any unprivileged user who executes the first swap after the pool is live can trade at an arbitrarily large oracle price jump without the guard firing.

---

### Finding Description

`PriceVelocityGuardExtension.beforeSwap` reads the stored mid-price, writes the new mid-price, then gates the velocity comparison behind `if (prevMid != 0)`:

```solidity
// PriceVelocityGuardExtension.sol lines 53-76
PriceVelocityState storage s = priceVelocityState[pool_];
uint128 prevMid = s.lastMidPriceX64;
uint64 prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64 = midPrice;          // state written unconditionally
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {                     // entire velocity check skipped when 0
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        ...
        if (actualSq > allowedSq) {
            revert PriceVelocityExceeded(actualSq, allowedSq);
        }
    }
}
``` [1](#0-0) 

The extension provides `setLastMidPrice` for the admin to seed the stored price, but it is `onlyPoolAdmin` and is never called automatically. There is no `initialize` override in `PriceVelocityGuardExtension` that would set `lastMidPriceX64` at pool creation time. [2](#0-1) 

The pool's `swap` function fetches the oracle price and passes it to `_beforeSwap` before executing any trade: [3](#0-2) 

Because `_beforeSwap` is called with the live oracle bid/ask, the extension receives a real, potentially large price. With `prevMid == 0`, the guard returns `IMetricOmmExtensions.beforeSwap.selector` without checking whether the price moved beyond `maxChangePerBlockE18`.

---

### Impact Explanation

The velocity guard's purpose is to cap how fast the oracle-derived mid-price can move between blocks, protecting LPs from oracle manipulation or flash-crash exploitation. Bypassing it on the first swap means:

- A swap that would normally be rejected (e.g., oracle price doubled in one block) executes without restriction.
- LP bins are repriced at the new oracle mid, and the swap math drains the bin at the manipulated/crashed price.
- LP principal is directly at risk: the attacker receives tokens at a price the guard was configured to block.

This matches the "bad-price execution" and "direct loss of LP assets" impact categories.

---

### Likelihood Explanation

- The trigger is fully unprivileged: any address can call `swap` and be the first swapper.
- The window is open from pool deployment until either the admin calls `setLastMidPrice` or the first swap naturally closes it.
- Pools are expected to go live with liquidity already deposited, so the window coincides with real LP exposure.
- The admin has no on-chain prompt or enforcement to call `setLastMidPrice` first; the extension's `initialize` override is the base no-op from `BaseMetricExtension`. [4](#0-3) 

---

### Recommendation

Override `initialize` in `PriceVelocityGuardExtension` to accept and store an initial mid-price (and optionally `maxChangePerBlockE18`), so the guard is armed atomically at pool creation:

```solidity
function initialize(address pool, bytes calldata data)
    external override onlyFactory returns (bytes4)
{
    (uint128 initialMid, uint64 maxChange) = abi.decode(data, (uint128, uint64));
    PriceVelocityState storage s = priceVelocityState[pool];
    s.lastMidPriceX64   = initialMid;
    s.lastUpdateBlock   = uint64(block.number);
    s.maxChangePerBlockE18 = maxChange;
    return IMetricOmmExtensions.initialize.selector;
}
```

Alternatively, add a guard in `beforeSwap` that reverts when `prevMid == 0` and `maxChange != 0`, forcing the admin to seed the price before the guard can be active.

---

### Proof of Concept

**Setup**

1. Factory deploys a pool with `PriceVelocityGuardExtension` as `EXTENSION_1` and `BEFORE_SWAP_ORDER = 1`.
2. Pool admin calls `setMaxChangePerBlock(pool, 1e16)` — capping moves at 1 % per block.
3. Admin does **not** call `setLastMidPrice`; `lastMidPriceX64 == 0`.
4. LPs deposit into the pool at oracle mid = 1000 USDC/ETH.

**Attack**

5. Oracle price jumps to 1200 USDC/ETH in the same block (20 % move — far above the 1 % cap).
6. Attacker calls `pool.swap(...)` with `zeroForOne = false` (buy ETH with USDC).
7. Pool fetches oracle bid/ask → mid ≈ 1200. Calls `_beforeSwap` → `PriceVelocityGuardExtension.beforeSwap`.
8. Inside the extension: `prevMid = s.lastMidPriceX64 = 0`. The `if (prevMid != 0)` branch is skipped entirely. The function returns the valid selector.
9. Swap executes at the 1200 mid. LPs who deposited at 1000 absorb the full 20 % repricing loss.
10. After this swap, `lastMidPriceX64 = 1200`; subsequent swaps are now protected — but the damage is done.

**Expected vs Actual**

| | Expected | Actual |
|---|---|---|
| Velocity check | Fires, reverts with `PriceVelocityExceeded` | Silently skipped |
| LP exposure | Capped at 1 %/block | Full 20 % move absorbed |

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

**File:** metric-core/contracts/MetricOmmPool.sol (L227-240)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L41-43)
```text
  function initialize(address, bytes calldata) external virtual onlyFactory returns (bytes4) {
    return IMetricOmmExtensions.initialize.selector;
  }
```
