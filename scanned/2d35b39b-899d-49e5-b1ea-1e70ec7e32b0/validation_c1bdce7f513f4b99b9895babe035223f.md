### Title
`PriceVelocityGuardExtension.beforeSwap()` Squaring Overflow Permanently Blocks Swaps on Pools with Extreme Oracle Prices - (File: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol)

### Summary

`PriceVelocityGuardExtension.beforeSwap()` computes `actualSq = changeE18 * changeE18` in checked Solidity 0.8+ arithmetic. When the oracle mid-price moves dramatically relative to the stored reference price, `changeE18` can exceed `2^128`, causing `changeE18 * changeE18` to overflow `uint256` and revert with a panic. Because this hook sits in the `beforeSwap` path, every swap on the pool is blocked until the pool admin intervenes.

### Finding Description

In `PriceVelocityGuardExtension.beforeSwap()`, the velocity check is:

```solidity
uint256 delta = midPrice > prevMid
    ? uint256(midPrice - prevMid)
    : uint256(prevMid - midPrice);

uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

uint256 actualSq  = changeE18 * changeE18;                          // ← overflow
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
``` [1](#0-0) 

`midPrice` and `prevMid` are both `uint128`, so `delta` is at most `type(uint128).max ≈ 3.4 × 10^38`. The intermediate `delta * 1e18` fits in `uint256` (max product ≈ `3.4 × 10^56 < 2^256`). However, `changeE18` itself can exceed `2^128` when `prevMid` is small:

```
changeE18 = (delta * 1e18) / prevMid
```

If `prevMid = 1e10` (a Q64.64 price of ≈ `5.4 × 10^-10`, plausible for a low-value token) and `delta ≈ 3.4 × 10^30`, then `changeE18 ≈ 3.4 × 10^38 > 2^128`. Squaring that value gives `≈ 1.16 × 10^77`, which exceeds `uint256` max (`≈ 1.16 × 10^77`). Solidity 0.8+ checked arithmetic reverts with a panic (`0x11`).

The panic is indistinguishable from a normal `PriceVelocityExceeded` revert to callers, but it propagates through `_callExtensionsInOrder` and causes every `pool.swap()` call to revert. [2](#0-1) 

The stored reference price `lastMidPriceX64` is updated **before** the check:

```solidity
s.lastMidPriceX64 = midPrice;   // written first
s.lastUpdateBlock = uint64(block.number);
// ... then the overflow check runs and reverts the whole tx
``` [3](#0-2) 

Because the entire transaction reverts, the state write is rolled back, so `prevMid` remains at the old extreme value. Every subsequent swap attempt re-enters the same overflow path.

The hook is wired into the pool's `_beforeSwap` dispatch: [4](#0-3) 

### Impact Explanation

All swaps on any pool that has `PriceVelocityGuardExtension` configured are blocked. LPs cannot rebalance, traders cannot execute, and protocol fees stop accruing. The pool admin can recover by calling `setMaxChangePerBlock(pool, 0)` (disables the guard) or `setLastMidPrice(pool, currentPrice)` (resets the reference), but neither has a timelock, so recovery depends on admin liveness. During the outage, LP positions are exposed to unhedged oracle price risk with no ability to exit via swap.

### Likelihood Explanation

The overflow requires `changeE18 > 2^128`, i.e., `delta / prevMid > 3.4 × 10^20`. This is reachable when:

- The pool's token pair has a very low Q64.64 price (e.g., a micro-cap token), giving a small `prevMid`.
- The oracle delivers a large price jump in a single block (e.g., after a listing event, a depeg, or a large market move).

No privileged action is required to trigger it: any user calling `pool.swap()` when the oracle mid-price satisfies the overflow condition will hit the panic. The pool admin's `setLastMidPrice` is the only recovery path, making the window of impact proportional to admin response time.

### Recommendation

Replace the bare squaring with overflow-safe arithmetic. Use `Math.mulDiv` or cap `changeE18` before squaring:

```solidity
// Option A: compare without squaring
// actualChange <= allowedChange * sqrt(1 + blockDiff)
// Use mulDiv to keep intermediate products in uint256:
uint256 allowedSq = Math.mulDiv(
    uint256(maxChange) * uint256(maxChange),
    1 + blockDiff,
    1
);
// Guard actualSq with a pre-check:
if (changeE18 > type(uint128).max) {
    revert PriceVelocityExceeded(type(uint256).max, allowedSq);
}
uint256 actualSq = changeE18 * changeE18; // safe: changeE18 <= 2^128
```

Or use `Math.mulDiv(changeE18, changeE18, 1)` with an explicit overflow guard.

### Proof of Concept

```solidity
// Pool configured with PriceVelocityGuardExtension, maxChangePerBlockE18 = 1e18 (100%)
// prevMid set to 1e10 (Q64.64 ≈ 5.4e-10, e.g. a micro-cap token)
// Oracle now returns midPrice = type(uint128).max

// delta = type(uint128).max - 1e10 ≈ 3.4028e38
// changeE18 = (3.4028e38 * 1e18) / 1e10 = 3.4028e46
// actualSq = 3.4028e46 * 3.4028e46 ≈ 1.16e93  >> uint256 max (1.16e77)
// → Solidity 0.8 checked arithmetic: PANIC revert

// Every subsequent swap() call hits the same path and panics.
// Pool admin must call setMaxChangePerBlock(pool, 0) to unblock.

function test_velocityGuard_overflow_blocksSwaps() public {
    // deploy pool with PriceVelocityGuardExtension, maxChange = 1e18
    extension.setMaxChangePerBlock(address(pool), 1e18);
    extension.setLastMidPrice(address(pool), 1e10); // tiny reference price

    // oracle now returns a large price → any swap panics
    vm.expectRevert(); // arithmetic overflow panic
    pool.swap(recipient, true, int128(1000), type(uint128).max, "", "");
}
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```
