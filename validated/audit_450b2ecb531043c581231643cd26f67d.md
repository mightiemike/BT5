### Title
SwapAllowlistExtension gates the router address instead of the end user, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the pool admin allowlists the router (a necessary step to let allowlisted users use the router), every unpermissioned user can bypass the allowlist by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of the `beforeSwap` call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed by `msg.sender` (the pool) and `sender` (whoever called `pool.swap()`): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` on behalf of the user. From the pool's perspective `msg.sender` is the **router**, so `sender` delivered to the extension is the router address, not the original user: [4](#0-3) 

The allowlist therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

**Broken invariant (analog to the external report):** In the AToken bug, a balance was zeroed during disqualification so that subsequent operations subtracted from the wrong (zeroed) state, producing an incorrect outcome. Here, the identity used in the allowlist check is replaced with the wrong actor (router instead of user) during the hook dispatch, so the guard evaluates the wrong identity and produces an incorrect access-control decision. Both are "wrong-state-used-in-critical-check" bugs triggered by a legitimate intermediate operation.

---

### Impact Explanation

A pool admin who wants allowlisted users to be able to trade through the router must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any** address — including addresses the admin explicitly excluded — can call `router.exactInputSingle` and pass the allowlist check, because the check resolves to `allowedSwapper[pool][router] == true`. The curated pool's access control is completely defeated for all router-mediated swaps. Unauthorized users can drain liquidity from a pool that was designed to serve only a restricted set of counterparties.

---

### Likelihood Explanation

The router is the canonical user-facing entry point documented and deployed alongside the core. Any pool admin who wants allowlisted users to enjoy slippage protection, multi-hop routing, or deadline enforcement through the router will naturally allowlist the router. The bypass is therefore reachable on any production pool that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter` and has at least one allowlisted user who prefers the router. No special privileges or malicious setup are required beyond a standard admin configuration.

---

### Recommendation

The extension must gate the **original user**, not the intermediate contract. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. The pool's `onlyPool` guard already ensures only a legitimate pool can call the extension, so the encoded value cannot be spoofed by an external caller.

2. **Check `sender` only for direct pool calls; require the router to forward the real user**: Add a thin wrapper in the router that encodes the original caller and have the extension decode it, similar to how Uniswap v4 passes the `hookData` originator.

Either way, `allowedSwapper[pool][sender]` must resolve to the economically relevant actor, not the routing intermediary.

---

### Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowAllSwappers(pool, false).
3. Pool admin calls setAllowedToSwap(pool, alice, true).   // alice is the only allowed user
4. Pool admin calls setAllowedToSwap(pool, router, true).  // necessary so alice can use the router

Attack
──────
5. bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
6. Router calls pool.swap(recipient=bob, ...) → msg.sender to pool = router.
7. Pool calls extension.beforeSwap(sender=router, ...).
8. Extension checks allowedSwapper[pool][router] == true → passes.
9. bob's swap executes on the curated pool despite never being allowlisted.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [5](#0-4) [4](#0-3)

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
