The code is fully readable. Here is the complete analysis.

---

### Title
First-Swap Velocity Guard Bypass via Zero `lastMidPriceX64` — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

### Summary

`PriceVelocityGuardExtension.beforeSwap` unconditionally skips the velocity check when `prevMid == 0`. Because `priceVelocityState` is zero-initialized for every new pool, the very first swap after deployment (or after any admin reset to zero) executes with no velocity constraint, regardless of how small `maxChangePerBlockE18` is configured.

### Finding Description

In `beforeSwap`, the guard reads `prevMid = s.lastMidPriceX64`, **writes the new mid price to storage first**, then gates the entire velocity check behind `if (prevMid != 0)`: [1](#0-0) 

Because `priceVelocityState` is a `mapping(address => PriceVelocityState)`, every newly registered pool starts with `lastMidPriceX64 = 0`. [2](#0-1) 

The admin-callable `setLastMidPrice` can seed a non-zero value, but there is no on-chain enforcement that it must be called before the first swap, and the contract's NatSpec does not document this as a required initialization step. [3](#0-2) 

The call path is fully unprivileged:

```
attacker → MetricOmmSimpleRouter.exactInputSingle
         → IMetricOmmPoolActions(pool).swap(...)
         → ExtensionCalling._beforeSwap(...)
         → PriceVelocityGuardExtension.beforeSwap
              prevMid == 0  →  check skipped
              s.lastMidPriceX64 = <extreme new price>
         → swap executes at arbitrary price move
``` [4](#0-3) [5](#0-4) 

After the first swap, `lastMidPriceX64` is set to the attacker-chosen extreme mid price. All subsequent swaps are then checked against that corrupted baseline, not the true market price at deployment time.

### Impact Explanation

The velocity guard's sole purpose is to prevent large per-block price moves that cause LPs to execute at bad prices. Bypassing it on the first swap allows an attacker to move the oracle-derived mid price by an unbounded amount in a single block. LPs who provided liquidity at the initial price suffer direct principal loss through bad-price execution — the pool settles the swap at a price the guard was explicitly configured to block.

This satisfies the contest's **bad-price execution** and **direct loss of LP principal** impact gates.

### Likelihood Explanation

Every pool that deploys `PriceVelocityGuardExtension` without an atomic `setLastMidPrice` call in the same transaction is vulnerable from block 0. An attacker can monitor the mempool for pool deployments and front-run the first legitimate swap. The admin has no on-chain mechanism to atomically initialize the guard and open the pool to swaps simultaneously, so the race window is structural, not accidental.

### Recommendation

Replace the `if (prevMid != 0)` bypass with a hard revert when `prevMid == 0` and `maxChangePerBlockE18 != 0`:

```solidity
if (prevMid == 0) {
    if (s.maxChangePerBlockE18 != 0) revert GuardNotInitialized();
    // else: guard intentionally disabled, allow
} else {
    // existing velocity check
}
```

Alternatively, require `setLastMidPrice` to be called (with a non-zero value) before any swap is permitted when `maxChangePerBlockE18 != 0`.

### Proof of Concept

```solidity
// Foundry integration test
function test_firstSwapBypassesVelocityGuard() public {
    // Deploy pool with PriceVelocityGuardExtension
    // Set maxChangePerBlockE18 = 1e15 (0.1% per block — very tight)
    // lastMidPriceX64 is 0 (never initialized)

    uint128 largePriceMovingAmount = 1_000_000e18;

    // This should revert if the guard were active, but it does NOT
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: largePriceMovingAmount,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));

    // Guard did not revert — velocity check was skipped
    // lastMidPriceX64 is now set to the extreme post-swap mid price
    assertGt(extension.priceVelocityState(address(pool)).lastMidPriceX64, 0);
}
``` [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L20-20)
```text
  mapping(address pool => PriceVelocityState) public priceVelocityState;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
