### Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool — which is the pool's own `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users simultaneously opens the pool to every user on the internet.

---

### Finding Description

**Call path — direct swap (correct):**

1. User calls `pool.swap(...)` directly.
2. Pool calls `_beforeSwap(msg.sender = user, ...)`.
3. `ExtensionCalling._beforeSwap` encodes `sender = user` and calls the extension.
4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][user]`. ✅

**Call path — router swap (broken):**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`.
3. Pool calls `_beforeSwap(msg.sender = router, ...)`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and calls the extension.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]`. ❌

The pool passes its own `msg.sender` as the `sender` argument to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` faithfully forwards that value: [2](#0-1) 

The extension then uses `msg.sender` (the pool) as the mapping key and the forwarded `sender` (the router) as the identity to check: [3](#0-2) 

The router always passes itself as the pool's caller: [4](#0-3) 

**The bypass scenario:**

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, every user — including those explicitly excluded — can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and the extension will pass them, because it only sees `sender = router`.

The same structural problem applies to the multi-hop `exactInput` path, where intermediate hops use `address(this)` (the router) as the payer, and the pool still sees `msg.sender = router`: [5](#0-4) 

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a specific set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege; the router is a public, permissionless contract. Unauthorized swaps drain LP value at oracle-derived prices, causing direct loss of LP principal and protocol fees on every trade that should have been blocked.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entry point for end users. Any pool admin who configures a swap allowlist and also wants to support the router (the normal operational assumption) will add the router to the allowlist, triggering the bypass. The attacker needs only to call the public router with the target pool address. No privileged access, no malicious setup, and no non-standard token behavior is required.

---

### Recommendation

The extension must gate on the **end user**, not on the intermediate caller. Two approaches:

1. **Pass the original user through the router.** The router stores the original `msg.sender` in transient storage (it already does this for the payer in `_setNextCallbackContext`). The pool could read a "real sender" hint from the router via a callback or a standardized transient slot, and pass that to the extension instead of its own `msg.sender`.

2. **Check `recipient` instead of `sender` for the swap allowlist.** The `recipient` is the address that receives output tokens and is set by the end user. For single-hop swaps this is the user-controlled destination. However, for multi-hop swaps the intermediate recipient is `address(this)` (the router), so this approach also breaks for multi-hop.

3. **Recommended fix:** Require that the pool's `msg.sender` (the router) implements a `swapOriginator()` view that returns the true end user, and have the extension call it when `msg.sender` is a known router. Alternatively, restrict the allowlist to direct-pool-call semantics only and document that router-mediated swaps are not subject to the allowlist (which changes the security model).

The simplest safe fix is to add a `trustedForwarder` concept: the extension checks `allowedSwapper[pool][sender]` when `sender` is not a registered forwarder, and checks `allowedSwapper[pool][<original user from forwarder>]` when it is.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured.
// 2. Pool admin allowlists alice: allowedSwapper[pool][alice] = true
// 3. Pool admin also allowlists the router so alice can use it:
//    allowedSwapper[pool][router] = true
// 4. Bob (not allowlisted) calls the router:

function testBypass() public {
    // alice is allowlisted, bob is not
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true); // needed for alice to use router

    // Bob is NOT allowlisted — direct swap reverts correctly
    vm.prank(bob);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(bob, false, int128(1000), type(uint128).max, "", "");

    // Bob routes through the router — extension sees sender=router, which IS allowlisted
    // Bob's swap succeeds despite not being on the allowlist
    deal(address(token1), bob, 10_000);
    vm.startPrank(bob);
    token1.approve(address(router), type(uint256).max);
    uint256 out = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token1),
            recipient: bob,
            deadline: type(uint256).max,
            amountIn: 1000,
            amountOutMinimum: 0,
            zeroForOne: false,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // Bob received tokens — allowlist bypassed
    assertGt(out, 0);
    vm.stopPrank();
}
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
