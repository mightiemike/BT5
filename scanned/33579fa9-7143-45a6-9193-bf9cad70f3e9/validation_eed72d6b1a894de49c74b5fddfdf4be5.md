### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address as `sender` instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates pool swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When any user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address — not the actual user's address. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for their curated pool), every unprivileged user bypasses the allowlist entirely.

---

### Finding Description

**Call chain when a user swaps through the router:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ..., extensionData)   // msg.sender = router
     → _beforeSwap(msg.sender=router, ...)
     → extension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router]  ← checked, NOT the actual user
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` argument: [3](#0-2) 

When the router calls `pool.swap()`, `sender` = router address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no user-identity forwarding: [4](#0-3) 

**Contrast with `DepositAllowlistExtension`**, which correctly ignores `sender` (the immediate caller / liquidity adder) and gates on `owner` (the economically relevant position owner): [5](#0-4) 

The test suite confirms the design: `FullMetricExtensionTest.test_allowedSwapSucceeds` allowlists `callers[0]` (the `TestCaller` contract that directly calls the pool), not `users[0]` (the EOA). When the router replaces `TestCaller` as the immediate pool caller, the checked identity changes to the router. [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address — the natural action to permit router-mediated swaps for their approved users — inadvertently opens the gate to every user. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) targeting the pool and the `beforeSwap` hook will pass because `allowedSwapper[pool][router] == true`. The allowlist provides zero protection: unauthorized users can execute swaps, extract value from LP positions at oracle-derived prices, and accumulate fees that should have been restricted to approved counterparties.

---

### Likelihood Explanation

The trigger is a plausible, non-malicious pool admin action. A pool admin who wants to allow router-mediated swaps for their allowlisted users has no other option than to allowlist the router address — the extension provides no mechanism to gate the underlying user when the router is the immediate caller. The bypass is then reachable by any unprivileged user with a single public call to the router. No special timing, flash loan, or privileged access is required.

---

### Recommendation

Gate on the actual economic actor, not the immediate pool caller. For swaps, the closest equivalent to `owner` in the deposit path is the `recipient` argument (the address receiving output tokens). Alternatively, require the actual user identity to be passed through `extensionData` and verified with a signature, or document that `SwapAllowlistExtension` is incompatible with the router and enforce this at the factory level by rejecting pools that configure both.

Minimal fix — check `recipient` instead of `sender` (mirrors `DepositAllowlistExtension` checking `owner` instead of `sender`):

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Pool admin deploys pool with SwapAllowlistExtension.
// 2. Pool admin allowlists the router so approved users can swap via it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 3. Pool admin allowlists only alice for direct swaps:
swapExtension.setAllowedToSwap(address(pool), alice, true);
// bob is NOT allowlisted.

// Attack: bob bypasses the allowlist via the router.
// bob calls:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         token1,
        recipient:       bob,
        zeroForOne:      false,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   type(uint128).max,
        deadline:        block.timestamp + 1,
        extensionData:   ""
    })
);
// pool.swap() is called with msg.sender = router.
// _beforeSwap passes sender = router to the extension.
// Extension checks allowedSwapper[pool][router] == true → passes.
// Bob's swap executes despite not being on the allowlist.
``` [3](#0-2) [7](#0-6) [1](#0-0)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
