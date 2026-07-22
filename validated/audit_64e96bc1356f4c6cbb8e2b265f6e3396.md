### Title
SwapAllowlistExtension gates the router address instead of the real user, allowing any caller to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the user. If the pool admin whitelists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist entirely by routing through the public router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → ExtensionCalling._beforeSwap(sender=router, recipient=user, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]   ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, i.e. `allowedSwapper[pool][router]`: [3](#0-2) 

There are two broken outcomes:

**Outcome A – router is whitelisted (bypass):** The pool admin whitelists the router so that router-mediated swaps work. Because the check is on the router address, every user who calls `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` passes the allowlist regardless of whether they are individually permitted. The per-user allowlist is completely inoperative.

**Outcome B – router is not whitelisted (DoS):** The pool admin whitelists individual users but not the router. Every whitelisted user is blocked from using the router even though they are explicitly permitted, because the check resolves to `allowedSwapper[pool][router] == false`.

The same identity mismatch applies to every multi-hop path in `exactInput` and `exactOutput`, where intermediate hops also arrive at the pool with `msg.sender = router`. [4](#0-3) 

---

### Impact Explanation

**Outcome A** is the fund-impacting path. A pool restricted to a curated set of counterparties (e.g., KYC'd LPs, protocol-owned addresses) can be freely traded against by any public user through the router. The pool's LP assets are exposed to unrestricted swap flow, defeating the economic protection the allowlist was meant to provide. This matches the "broken core pool functionality" and "admin-boundary break" impact categories.

**Outcome B** renders the router unusable for all whitelisted users on allowlisted pools, breaking the primary user-facing swap path.

---

### Likelihood Explanation

The router is a public, permissionless contract. Any user can call it. A pool admin who deploys a `SwapAllowlistExtension` and wants to support the standard router flow will naturally whitelist the router address — triggering Outcome A. No privileged cooperation from the attacker is required; the bypass is reachable by any EOA calling `exactInputSingle`.

---

### Recommendation

The extension must gate the **economically relevant actor**, not the intermediary. Two options:

1. **Pass the real user through the router.** Add a `swapper` field to the router's `extensionData` payload and have the extension decode and verify it. This requires the extension to trust the pool's `sender` only when it is a known router, then fall back to the decoded field.

2. **Check `sender` only when it is not a known router; otherwise check a user-supplied field.** The cleanest fix is to have the router forward `msg.sender` (the real user) inside `extensionData` and have `SwapAllowlistExtension` decode it when `sender` is a recognized router address.

The simplest safe fix: require that `sender` always be the real user by having the router pass `msg.sender` as the `sender` argument to `pool.swap` rather than letting the pool use `msg.sender` of the call. However, since `sender` is set by the pool from its own `msg.sender`, the fix must be in the extension's interpretation of the payload, not in the pool.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin whitelists alice and the router (to allow router-mediated swaps).
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT whitelisted.
// bob calls the router directly — the pool sees msg.sender = router, which IS whitelisted.
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        tokenIn: address(token0),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds — bob bypassed the per-user allowlist.
vm.stopPrank();

// Verify: bob is not individually whitelisted.
assertFalse(extension.isAllowedToSwap(address(pool), bob));
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
