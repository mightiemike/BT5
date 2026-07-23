The call chain is fully traceable in the scoped code. Let me confirm the exact binding at each step.

The call chain is fully confirmed. Here is the exact binding at each step:

**Step 1:** Attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` — attacker is `msg.sender` to the router.

**Step 2:** Router calls `pool.swap(...)` — the router is now `msg.sender` to the pool. [1](#0-0) 

**Step 3:** Pool calls `_beforeSwap(msg.sender, ...)` — `msg.sender` here is the **router address**, not the original attacker. [2](#0-1) 

**Step 4:** `SwapAllowlistExtension.beforeSwap` receives `sender = router_address` and checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`. [3](#0-2) 

The extension never sees the original caller. If `allowedSwapper[pool][router] = true`, every user who routes through the router passes the check unconditionally.

---

### Title
`SwapAllowlistExtension` checks the router address instead of the original caller, allowing any user to bypass per-user swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which the pool binds to `msg.sender` at the time `pool.swap` is called. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the original user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. Any pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the gate to every user on the internet.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(msg.sender, recipient, ...);   // msg.sender = router when called via router
``` [4](#0-3) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [5](#0-4) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) revert ...
//                   ^^^pool^^^                              ^^^router^^^
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` stores the original caller only in the transient callback context (for payment), but never passes it to the pool or extension: [6](#0-5) 

The extension has no mechanism to recover the original caller. The design creates an inescapable dilemma for any pool admin:

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — even for allowlisted users |
| Router **allowlisted** | All users bypass the allowlist via the router |

There is no configuration that achieves the intended goal of per-user allowlisting with router support.

### Impact Explanation
Any non-allowlisted user can swap on a pool that is supposed to be access-controlled (e.g., KYC-gated, institutional, or counterparty-restricted) simply by routing through `MetricOmmSimpleRouter`. The allowlist invariant — "only allowlisted addresses may swap" — is broken for all router-mediated swaps whenever the router is allowlisted. This constitutes broken core functionality of the extension.

### Likelihood Explanation
The trigger condition (router address in the allowlist) is the natural and only viable configuration for any pool admin who wants to support router-mediated swaps for their approved users. It requires no malicious intent and no unusual setup — it is the expected operational state for any allowlisted pool that integrates with the periphery router.

### Recommendation
The extension must gate on the **original caller**, not the intermediary. Two viable approaches:

1. **Pass original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Check `tx.origin` as a fallback:** When `sender` is a known router, fall back to `tx.origin`. This is fragile and generally discouraged.
3. **Preferred — router forwards original caller explicitly:** Add a dedicated field to the swap call or a separate router-aware extension interface that carries the original payer identity, so the extension can check the economically relevant actor.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists the router: allowedSwapper[pool][router] = true
// 3. Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

function test_swapAllowlist_bypassViaRouter() public {
    // attacker is NOT in the allowlist
    assertFalse(extension.isAllowedToSwap(address(pool), attacker));
    // router IS in the allowlist (admin added it to enable router swaps)
    assertTrue(extension.isAllowedToSwap(address(pool), address(router)));

    vm.prank(attacker);
    // attacker routes through the router — extension sees sender=router, not sender=attacker
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: attacker,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // swap succeeds despite attacker not being allowlisted
    assertGt(amountOut, 0);
}
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
