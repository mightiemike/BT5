### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Per-User Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` parameter, which is `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps — a natural UX action — any user can bypass the per-user allowlist by routing through the router.

### Finding Description
`SwapAllowlistExtension.beforeSwap()` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the caller of the extension hook) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of `pool.swap()`.

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

When a user swaps through `MetricOmmSimpleRouter.exactInputSingle()`, the call chain is:

1. User → `router.exactInputSingle()`
2. Router → `pool.swap()` (router is `msg.sender`)
3. Pool → `extension.beforeSwap(sender = router, ...)`
4. Extension checks `allowedSwapper[pool][router]`

The extension sees the **router address**, not the actual user. The router is a public, permissionless contract — anyone can call it. If the pool admin allowlists the router (to enable router-mediated swaps for their users), the allowlist is effectively open to every address in existence. The extension has no mechanism to recover the actual user's identity from the router call.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the position owner), which the liquidity adder passes through unchanged from the caller's input. The swap path has no equivalent owner-style argument — only `sender` (the immediate caller) and `recipient` (the output destination).

### Impact Explanation
Any user can bypass a pool's per-user swap allowlist by routing through `MetricOmmSimpleRouter` once the router address is allowlisted. A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC-verified addresses, designated market makers, or protocol-controlled accounts) loses that restriction entirely for the router path. Unauthorized traders gain full swap access to the pool, exposing LP funds to flow the pool was designed to exclude. Because swaps are oracle-anchored, the direct per-swap loss is bounded by the spread, but the aggregate LP exposure to unintended counterparties is unbounded over time.

### Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router address. This is a natural and expected operational step: any pool that wants its LPs to be reachable via the standard periphery router must allowlist it. The admin is likely unaware that doing so grants unrestricted swap access to every address, because the extension's name and setter (`setAllowedToSwap`) imply per-address granularity. No attacker capability beyond calling the public router is required once the router is allowlisted.

### Recommendation
1. **Short-term**: Document explicitly that allowlisting the router grants swap access to all router users, not to specific individuals. Warn pool admins against allowlisting shared public routers when per-user gating is intended.
2. **Medium-term**: Extend the router to forward the originating user's address in `extensionData` (e.g., `abi.encode(msg.sender)`). Update `SwapAllowlistExtension` to decode and check this field when `sender` is a known router, falling back to `sender` otherwise.
3. **Long-term**: Consider a two-level check: gate on `sender` for direct callers and on a decoded user field for router-mediated calls, with the router signing or attesting the user identity so the extension can trust it.

### Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls extension.setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for their users.
3. Attacker (address never individually allowlisted) calls:
     router.exactInputSingle({pool: pool, ...})
4. Call chain:
     router → pool.swap(msg.sender=router) →
     extension.beforeSwap(sender=router) →
     allowedSwapper[pool][router] == true → passes
5. Attacker's swap executes on the restricted pool.
   No individual allowlist entry for the attacker exists;
   the per-user gate is fully bypassed.
``` [1](#0-0) 

The extension receives `sender` = `msg.sender` of `pool.swap()`: [2](#0-1) 

The router calls `pool.swap()` directly, substituting itself as `msg.sender`: [3](#0-2) 

The pool dispatches `sender = msg.sender` to the

### Citations

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
