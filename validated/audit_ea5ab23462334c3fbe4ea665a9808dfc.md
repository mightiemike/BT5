The code confirms all three legs of the claim. Let me verify the exact lines before writing the report.

- `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()` at lines 230–231. [1](#0-0) 
- `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate pool caller at line 37. [2](#0-1) 
- `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router `msg.sender` to the pool at lines 72–80. [3](#0-2) 

---

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` checks the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router address, not the originating user. A pool admin who allowlists the router to enable their allowlisted users to use the standard interface inadvertently opens the pool to all users, completely bypassing the per-user allowlist.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // sender = immediate caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap()` checks this `sender` against the per-pool allowlist, where `msg.sender` is the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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

The pool therefore passes `sender = router` to `_beforeSwap`. The extension checks `allowedSwapper[pool][router]`, **not** `allowedSwapper[pool][actualUser]`. Once the router is allowlisted, any user can call `router.exactInputSingle()` and the extension check passes unconditionally, regardless of whether that user is individually allowlisted. The same flaw applies to `exactInput` multi-hop paths, where intermediate hops also use the router as `msg.sender` to each pool.

No existing guard in `SwapAllowlistExtension` reads `tx.origin`, `extensionData`, or any other field that would identify the originating user. The `_setNextCallbackContext` call in the router stores `msg.sender` only for the payment callback, not for the extension check.

## Impact Explanation
The swap allowlist protection is completely bypassed for all router-mediated swaps once the router is allowlisted. Any unprivileged user can trade on a supposedly curated/restricted pool by routing through `MetricOmmSimpleRouter`. LP funds are at direct risk if the pool was designed to only accept specific, trusted counterparties (e.g., institutional traders, KYC-verified users). The pool admin cannot fix this without removing the router from the allowlist entirely, which then breaks the router path for legitimately allowlisted users. This constitutes broken core pool functionality (allowlist access control) with direct fund impact on LP positions.

## Likelihood Explanation
A pool admin who deploys a curated pool with a swap allowlist and also wants their allowlisted users to use the standard router interface will naturally call `setAllowedToSwap(pool, router, true)`. This is a common and expected operational step. The bypass is non-obvious because the pool admin's mental model is "I am allowlisting the router so my users can use it," not "I am opening the pool to all users." The `SwapAllowlistExtension` NatSpec states it "Gates `swap` by swapper address, per pool" — the word "swapper" implies the economic actor, not the immediate pool caller, creating a false sense of security. The attack requires no special privileges: any user with access to the router can exploit it.

## Recommendation
The extension must check the actual economic actor, not the immediate caller of the pool. Concrete options:

1. **Router-forwarded identity**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router convention and the extension must validate the caller is a known router before trusting the encoded identity.
2. **Pool-level original sender**: The pool exposes a separate `originalSender` field that periphery contracts populate before calling `swap()`, and the extension reads it via a pool interface call.
3. **Documentation + safe default**: Clearly document that allowlisting the router opens the pool to all users. Provide a revert or warning in `setAllowedToSwap` when the target address is a known router. Recommend that curated pools never allowlist the router and instead require direct pool calls.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin: setAllowedToSwap(pool, user1, true)
   — intent: only user1 may swap.
3. Pool admin: setAllowedToSwap(pool, router, true)
   — intent: allow user1 to use the router conveniently.
4. Non-allowlisted user2 calls:
     router.exactInputSingle({pool: pool, recipient: user2, ...})
5. Router calls: pool.swap(user2, ...)  [router is msg.sender to pool]
6. Pool calls: _beforeSwap(sender=router, ...)
7. Extension checks: allowedSwapper[pool][router] → true
   — router is allowlisted, check passes.
8. Swap executes for user2 despite user2 not being individually allowlisted.
   — Allowlist completely bypassed.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
