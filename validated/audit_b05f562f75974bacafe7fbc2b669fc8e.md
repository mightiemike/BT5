### Title
SwapAllowlistExtension gates the router's address instead of the user's address, allowing any user to bypass a curated pool's swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` in the pool, so the extension checks the router's address — not the user's address. If the router is allowlisted (e.g., the pool admin adds it to permit router-based swaps), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
     → pool.swap(recipient, ...)          // msg.sender in pool = router
     → _beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist: [3](#0-2) 

When the router calls `pool.swap(...)`, `sender` = router address. The check becomes `allowedSwapper[pool][router]`. The actual user who initiated the call is never inspected.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no forwarding of the original `msg.sender`: [4](#0-3) 

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

Two fund-impacting scenarios follow directly:

**Scenario A — Allowlist bypass (Critical/High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a specific set of counterparties (e.g., KYC'd addresses or protocol-owned bots). The admin also adds the router to the allowlist so that allowlisted users can trade conveniently through the router. Because the extension checks the router's address, every user on the network — including those explicitly excluded — can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle`. The curated pool's entire access-control policy is nullified.

**Scenario B — Broken core functionality (High):** If the pool admin does *not* allowlist the router (the safe choice once the bug is understood), then no user can swap through the router even if they are individually allowlisted. The router — the primary user-facing swap entrypoint — is permanently broken for every allowlisted pool, forcing users to call the pool directly and losing slippage protection, multi-hop routing, and deadline enforcement.

Both outcomes are direct consequences of the wrong-actor binding: the extension was designed to gate individual users but gates the router contract instead.

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a first-class production extension shipped in `metric-periphery`.
- The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint.
- Any pool admin who deploys a curated pool and adds the router to the allowlist (a natural and expected configuration) immediately exposes the bypass to every user.
- No special privileges, flash loans, or multi-block manipulation are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must check the economically relevant actor — the original user — not the intermediary router. Two complementary fixes:

1. **Pass the original user through the router.** The router already stores the original `msg.sender` in transient storage as the payer. The pool's `swap` interface could accept an explicit `originator` argument, or the router could pass it inside `callbackData`/`extensionData` for the extension to decode. This is the cleanest fix.

2. **Check `recipient` instead of `sender` as a short-term mitigation.** For single-hop swaps the recipient is often the user. However, this is not reliable for multi-hop paths where intermediate recipients are the router itself.

3. **Document that the router must never be allowlisted** until the actor-binding is corrected, and add a factory-level guard that prevents the router address from being added to any per-pool allowlist.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Pool admin adds ROUTER address to allowedSwapper[pool][router] = true
//    (intending to allow router-based swaps for allowlisted users).
// 3. Alice (address 0xA11CE) is NOT in allowedSwapper[pool][alice].

// Attack:
// Alice calls:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:           pool,
        tokenIn:        token0,
        recipient:      alice,
        amountIn:       1e18,
        amountOutMinimum: 0,
        zeroForOne:     true,
        priceLimitX64:  0,
        deadline:       block.timestamp,
        extensionData:  ""
    })
);
// pool.swap(alice, true, ...) is called with msg.sender = router.
// _beforeSwap(sender=router, ...) → extension checks allowedSwapper[pool][router] = true → PASSES.
// Alice's swap executes despite not being in the allowlist.
// Result: curated pool's access control is fully bypassed.
```

The extension's check at line 37 of `SwapAllowlistExtension.sol` evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][alice]`, so the allowlist is silently bypassed for any user who routes through the public router. [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
