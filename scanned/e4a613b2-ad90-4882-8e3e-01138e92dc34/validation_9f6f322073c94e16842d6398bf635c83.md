### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract**, not the originating user. If the pool admin allowlists the router (the natural configuration for supporting router-mediated swaps), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain that exposes the bug:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         └─ pool.swap(recipient, ...)          // msg.sender = router
               └─ _beforeSwap(msg.sender, ...) // sender = router
                     └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                           └─ allowedSwapper[pool][router]  // checks router, not user
```

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is allowlisted: [2](#0-1) 

When the router calls `pool.swap()`, `sender` is the router address, not the originating user: [3](#0-2) 

The same identity mismatch applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

**Contrast with `DepositAllowlistExtension`**, which correctly ignores the `sender` parameter and checks `owner` — the actual position owner explicitly threaded through the call: [5](#0-4) 

The deposit extension works correctly because `MetricOmmPoolLiquidityAdder` passes the real user as `owner` to `pool.addLiquidity(owner, ...)`. No equivalent explicit-user parameter exists on the swap path; the only identity the extension sees is the direct caller of `pool.swap()`.

**The impossible dilemma for pool admins:**

| Router allowlisted? | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| No | Blocked (broken UX) | Blocked (correct) |
| Yes | Allowed (correct) | **Allowed (bypass)** |

A pool admin who wants to support router-mediated swaps for their allowlisted counterparties must allowlist the router. Once the router is allowlisted, the allowlist is completely defeated for all users.

---

### Impact Explanation

Any non-allowlisted user can trade in a pool that the admin intended to restrict to specific counterparties (e.g., KYC'd users, whitelisted market makers, private OTC pools). LP funds are exposed to unauthorized counterparties, defeating the core purpose of the `SwapAllowlistExtension`. This is a direct broken-core-functionality impact: the access-control extension cannot enforce its invariant on the standard public swap path.

---

### Likelihood Explanation

The trigger is realistic and requires no privileged access:

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific users.
2. To support normal UX (router-mediated swaps for those users), the admin also allowlists the router address.
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool.
4. The extension sees `sender = router`, which is allowlisted, and the swap succeeds.

The `MetricOmmSimpleRouter` is a public, permissionless contract. No special setup, malicious pool, or non-standard token is required.

---

### Recommendation

The swap path has no explicit "originating user" parameter analogous to `owner` on the liquidity path. Two viable fixes:

1. **Thread the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; `SwapAllowlistExtension.beforeSwap()` decodes and checks it. This requires a convention between router and extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps where the user is also the recipient, checking `recipient` would gate the correct actor. This breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Structural fix — add an explicit `swapper` parameter to `pool.swap()`**: Mirror the deposit path by adding a caller-supplied `swapper` identity that the pool passes to extensions, with the pool enforcing that the callback payer matches. This is the cleanest fix but requires a core interface change.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Scenario: pool admin allowlists alice and the router, intending only alice to swap.
// Bob (non-allowlisted) bypasses the allowlist via the router.

function test_swapAllowlist_bypassViaRouter() public {
    // Setup: deploy pool with SwapAllowlistExtension
    // Admin allowlists alice directly AND the router (to support alice's router usage)
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true); // <-- necessary for alice to use router

    // Add liquidity (alice is allowlisted depositor)
    depositExtension.setAllowedToDeposit(address(pool), alice, true);
    vm.prank(alice);
    pool.addLiquidity(alice, 0, deltas, callbackData, "");

    // Bob is NOT allowlisted — direct swap reverts correctly
    vm.prank(bob);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(bob, false, int128(1000), type(uint128).max, "", "");

    // Bob bypasses via router — succeeds because extension sees sender=router (allowlisted)
    token1.approve(address(router), type(uint256).max);
    vm.prank(bob);
    // This should revert but does NOT:
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        tokenIn: address(token1),
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    // Bob received token0 from a restricted pool — allowlist bypassed
}
```

The pool's `_beforeSwap` passes `msg.sender` (= router) as `sender` to the extension: [6](#0-5) 

The extension checks `allowedSwapper[pool][router]`, which is `true`, so the swap proceeds regardless of who called the router: [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
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
