### Title
Uninitialized `lastMidPriceX64` in `PriceVelocityGuardExtension` causes velocity guard to fail open on first swap — (File: `metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` stores per-pool velocity state in `priceVelocityState[pool_]`, a mapping whose entries default to all-zero. The guard's `beforeSwap` hook skips the velocity check entirely when `prevMid == 0`. Because `lastMidPriceX64` is never initialized at pool-registration time, the guard fails **open** on every pool's first swap, regardless of how extreme the oracle-derived mid-price is. An attacker can backrun pool creation, execute a swap before the admin calls `setLastMidPrice`, and trade at an unchecked price while LPs bear the adverse-selection loss.

---

### Finding Description

`PriceVelocityGuardExtension.beforeSwap` reads the stored previous mid-price and conditionally enforces the velocity cap:

```solidity
// PriceVelocityGuardExtension.sol lines 53-76
PriceVelocityState storage s = priceVelocityState[pool_];
uint128 prevMid = s.lastMidPriceX64;   // ← zero for any new pool
uint64  prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64  = midPrice;         // baseline written after the check
s.lastUpdateBlock  = uint64(block.number);

if (prevMid != 0) {                    // ← guard is entirely skipped when zero
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        // ... velocity arithmetic and revert
    }
}
``` [1](#0-0) 

The mapping entry for a newly-registered pool is zero-initialized by the EVM. `lastMidPriceX64 = 0` causes the outer `if (prevMid != 0)` branch to be skipped unconditionally, so no velocity arithmetic runs and no revert is possible. The hook returns the success selector, and the swap proceeds at whatever oracle price was presented.

The admin-callable `setLastMidPrice` is the only way to seed a non-zero baseline:

```solidity
// lines 29-33
function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64  = newLastMidPriceX64;
    s.lastUpdateBlock  = uint64(block.number);
    ...
}
``` [2](#0-1) 

There is no call to `setLastMidPrice` inside pool creation or extension initialization. The `BaseMetricExtension` `initialize` callback (called by the factory during pool deployment) does not write any velocity state, so the window between pool creation and the admin's manual `setLastMidPrice` call is unguarded. [3](#0-2) 

The pool's `_beforeSwap` passes the oracle-derived `bidPriceX64` / `askPriceX64` directly to the extension:

```solidity
// ExtensionCalling.sol lines 149-177
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified,
         priceLimitX64, packedSlot0Initial,
         bidPriceX64, askPriceX64, extensionData))
);
``` [4](#0-3) 

If the oracle returns a price that has moved far from the pool's expected range (flash crash, oracle update lag, or a legitimate large move), the velocity guard is the designated circuit-breaker. With `prevMid == 0` it is silently disabled.

---

### Impact Explanation

The velocity guard is the last on-chain defense against a rapidly-moved oracle price reaching the pool's swap math. When it fails open on the first swap:

- A swap executes at an oracle price that may have moved by an arbitrary percentage since the pool was seeded with liquidity.
- LPs suffer adverse selection: the counterparty trades at a price that the guard was configured to block, extracting value from LP reserves.
- The loss is bounded only by pool liquidity depth and the oracle's own staleness/price-guard checks, not by the velocity cap the admin intended to enforce.

This matches the allowed impact category: **bad-price execution** (an unclamped oracle quote reaches a pool swap) and **oracle guard path** (velocity guard fails open instead of closed on a zero/uninitialized prior observation).

---

### Likelihood Explanation

- Every pool that uses `PriceVelocityGuardExtension` starts with `lastMidPriceX64 == 0`.
- The admin must perform a separate, manual `setLastMidPrice` transaction after pool creation; there is no atomic initialization path.
- Any swap that lands in the block(s) between pool creation and that admin call bypasses the guard.
- An attacker monitoring the mempool can trivially backrun the pool-creation transaction with a swap, exploiting the unguarded window in the same block.
- No special permissions are required; any address that can call `pool.swap()` (or is allowlisted, if a swap allowlist is also configured) can trigger the bypass.

---

### Recommendation

Seed `lastMidPriceX64` atomically at pool registration time. The `BaseMetricExtension.initialize` callback is called by the factory during pool deployment and is the correct place to do this. The extension should query the pool's price provider for the current mid-price and write it to `priceVelocityState[pool_]` during `initialize`, so the guard is active from the very first swap.

Alternatively, change the guard logic to **fail closed** when `prevMid == 0` (i.e., revert if `maxChangePerBlockE18 != 0` but no baseline has been set), forcing the admin to explicitly seed the state before any swap is permitted.

---

### Proof of Concept

1. Admin deploys a pool with `PriceVelocityGuardExtension` attached and calls `setMaxChangePerBlock(pool, 1e16)` (1% per block cap).
2. Admin has not yet called `setLastMidPrice`. `priceVelocityState[pool].lastMidPriceX64 == 0`.
3. Oracle price moves 40% in one block (legitimate market event or oracle update lag).
4. Attacker observes the pool-creation transaction in the mempool and backruns it with `pool.swap(...)`.
5. `beforeSwap` is called: `prevMid = s.lastMidPriceX64 = 0`. The `if (prevMid != 0)` branch is skipped. The hook returns `IMetricOmmExtensions.beforeSwap.selector` — success.
6. The swap executes at the 40%-moved oracle price. The velocity cap of 1% is never enforced.
7. LPs lose funds proportional to the price deviation; the attacker captures the arbitrage that the guard was configured to prevent.
8. `s.lastMidPriceX64` is now written to the current (extreme) mid-price, so subsequent swaps are checked — but the damage from step 6 is already done.

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L61-107)
```text
  }

  function beforeRemoveLiquidity(address, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }

  function afterRemoveLiquidity(address, address, uint80, LiquidityDelta calldata, uint256, uint256, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }

  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }

  function afterSwap(
    address,
    address,
    bool,
    int128,
    uint128,
    uint256,
    uint256,
    uint128,
    uint128,
    int128,
    int128,
    uint256,
    bytes calldata
  ) external virtual onlyPool returns (bytes4) {
    revert ExtensionNotImplemented();
  }
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
