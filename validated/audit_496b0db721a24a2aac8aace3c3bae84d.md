Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the originating user's address, nullifying per-pool swap access control — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool boundary, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. This either allows every user on the network to bypass a curated pool's allowlist (if the router is allowlisted), or permanently breaks router-based swaps for all individually-allowlisted users (if the router is not allowlisted).

## Finding Description

**Root cause — wrong actor propagated as `sender`:**

`MetricOmmPool.swap` passes its own `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` receives it as `sender` and checks it against the per-pool allowlist (`msg.sender` here is the pool): [3](#0-2) 

**Router call path — router is `msg.sender` at the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, with no forwarding of the original `msg.sender`: [4](#0-3) 

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**Full exploit call chain:**
```
User (0xA11CE, not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(...)
    → pool.swap(recipient=alice, ...)        // msg.sender in pool = router
      → _beforeSwap(sender=router, ...)
        → ExtensionCalling._beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → checks allowedSwapper[pool][router] = true  ← PASSES
            // allowedSwapper[pool][alice] = false is never checked
```

No existing guard prevents this. The `_requireExpectedCallbackCaller` check in the router only validates the callback caller, not the swap initiator. The extension has no mechanism to recover the original user from `extensionData` or any other channel.

## Impact Explanation

**Scenario A — Allowlist bypass (Critical/High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses or protocol-owned bots. The admin adds the router to the allowlist so that allowlisted users can trade conveniently through it. Because the extension checks the router's address, every user on the network — including those explicitly excluded — can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle`. The curated pool's entire access-control policy is nullified. This is a direct admin-boundary break by an unprivileged path.

**Scenario B — Broken core functionality (High):** If the pool admin does not allowlist the router (the safe choice once the bug is understood), then no user can swap through the router even if they are individually allowlisted. The router — the primary user-facing swap entrypoint — is permanently broken for every allowlisted pool, constituting an unusable core swap flow.

Both outcomes meet the allowed impact gate: admin-boundary break and broken core pool functionality causing unusable swap flows.

## Likelihood Explanation

- `SwapAllowlistExtension` is a first-class production extension shipped in `metric-periphery`.
- `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint.
- Any pool admin who deploys a curated pool and adds the router to the allowlist (a natural and expected configuration) immediately exposes the bypass to every user.
- No special privileges, flash loans, or multi-block manipulation are required — a single `exactInputSingle` call suffices.
- The misconfiguration is invisible to the pool admin: the extension's `isAllowedToSwap` view function returns `true` for the router, giving no indication that the user-level check is broken. [6](#0-5) 

## Recommendation

The extension must check the economically relevant actor — the original user — not the intermediary router.

1. **Pass the original user through the router.** The router already stores the original `msg.sender` as the payer in transient storage. The pool's `swap` interface could accept an explicit `originator` argument, or the router could encode it inside `extensionData` for the extension to decode. This is the cleanest fix.

2. **Short-term mitigation:** Document that the router must never be allowlisted until the actor-binding is corrected, and add a factory-level guard that prevents the router address from being added to any per-pool allowlist.

3. **Check `recipient` as a partial mitigation** only for single-hop exact-input swaps where the recipient is the user — this is unreliable for multi-hop paths where intermediate recipients are the router itself.

## Proof of Concept

```solidity
// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Pool admin: allowedSwapper[pool][router] = true
//    (intending to allow router-based swaps for allowlisted users).
// 3. Alice (0xA11CE) is NOT in allowedSwapper[pool][alice].

// Attack — Alice calls:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             pool,
        tokenIn:          token0,
        recipient:        alice,
        amountIn:         1e18,
        amountOutMinimum: 0,
        zeroForOne:       true,
        priceLimitX64:    0,
        deadline:         block.timestamp,
        extensionData:    ""
    })
);
// pool.swap(alice, true, ...) called with msg.sender = router.
// _beforeSwap(sender=router) → allowedSwapper[pool][router] = true → PASSES.
// Alice's swap executes despite not being in the allowlist.
```

The check at line 37 of `SwapAllowlistExtension.sol` evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][alice]`, so the allowlist is silently bypassed for any user who routes through the public router. [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L27-29)
```text
  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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
