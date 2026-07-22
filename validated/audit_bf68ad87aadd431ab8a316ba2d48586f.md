The vulnerability is real and traceable through the code. Here is the full analysis:

---

### Title
SwapAllowlistExtension Gates Router Address Instead of Original User, Allowing Any EOA to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original EOA. If the router is allowlisted (the natural setup for a pool supporting router-based swaps), every EOA in existence can bypass the per-user gate by calling through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`**, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly**, making the router the pool's `msg.sender`. The original EOA (`msg.sender` of `exactInputSingle`) is never forwarded to the pool: [4](#0-3) 

**Result:** The allowlist check resolves to `allowedSwapper[pool][router]`. If the router is allowlisted (required for any router-based swap to work), every EOA bypasses the per-user gate. If the router is not allowlisted, no user can swap through the router at all — the extension is incompatible with the router.

---

### Impact Explanation

A pool deployer configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd users, whitelisted market makers). To support the standard router UX, the admin allowlists the router address. Any non-allowlisted EOA can then call `MetricOmmSimpleRouter.exactInputSingle` and execute a swap on the restricted pool, completely bypassing the intended access control. The allowlist provides zero per-user protection when the router is in use.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint. Any pool that uses `SwapAllowlistExtension` and also supports router-based swaps (the common case) is affected. No special privileges or unusual conditions are required — any EOA can exploit this by simply calling the public router.

---

### Recommendation

The extension should check the **original initiator**, not the immediate caller of `pool.swap`. Two options:

1. **Pass `tx.origin` as an additional parameter** in the hook interface (breaks composability and is generally discouraged).
2. **Require direct pool interaction** — document that `SwapAllowlistExtension` is incompatible with the router and enforce this at the extension or factory level.
3. **Preferred:** Redesign the hook interface to carry an `originator` field (set by the pool to `tx.origin` or via a signed permit), so extensions can gate the true initiating address regardless of intermediary contracts.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists ONLY the router address:
//    swapExtension.setAllowedToSwap(address(pool), address(router), true);
// 3. Non-allowlisted EOA calls router:

vm.prank(nonAllowlistedEOA); // not in allowedSwapper[pool]
uint256 amountOut = router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: nonAllowlistedEOA,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Swap succeeds — allowlist gated the router, not the user.
assertGt(amountOut, 0);
```

The extension checks `allowedSwapper[pool][router]` = `true`, so the swap proceeds even though `nonAllowlistedEOA` is not in the allowlist.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
