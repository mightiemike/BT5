Based on my analysis of the codebase, I found a valid analog. The `SwapAllowlistExtension` checks the direct caller of `pool.swap()` as the "swapper" identity. When users route through `MetricOmmSimpleRouter`, the pool sees the **router address** as `sender`, not the actual end user. If the pool admin allowlists the router (a natural configuration to enable router-based swaps), every user in the world can bypass the per-user allowlist by routing through the router.

---

### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any unprivileged caller to bypass a curated pool's swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-based swaps, the per-user allowlist is silently voided: any unprivileged address can swap by calling the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling` forwards that value unchanged to the extension's `beforeSwap(sender, ...)`.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool receives `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

A pool admin who wants to allow router-mediated swaps must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, **every address** can bypass the per-user allowlist by routing through the router, because the extension only sees the router address and approves it unconditionally.

### Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a whitelist of counterparties (e.g., KYC'd addresses, protocol-owned contracts, or specific market makers) is fully bypassed. Any unprivileged user can swap against the pool's liquidity by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This constitutes a broken core pool functionality: the allowlist guard fails open for all router-mediated swaps, exposing LP assets to unauthorized counterparties and potentially draining the pool through adverse selection or policy-violating trades.

### Likelihood Explanation
The router is the standard, documented periphery entry point for swaps. Pool admins who configure a `SwapAllowlistExtension` and also want to support router-based swaps for their allowlisted users must allowlist the router — there is no other mechanism. Once the router is allowlisted, the bypass is immediately available to any address with no special privileges, no admin cooperation, and no unusual token behavior required. The attacker only needs to call a public router function.

### Recommendation
The extension must gate the **economic actor** (the end user), not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router**: The router already tracks `msg.sender` in transient storage as the payer (`_setNextCallbackContext(..., msg.sender, ...)`). Extend this to also store the originating user and forward it as `extensionData` or a dedicated field so the extension can check the real caller.

2. **Check `recipient` or a user-supplied identity field in the extension**: Alternatively, require that the `sender` field passed to `beforeSwap` always be the end user, and enforce this at the router level by passing `msg.sender` explicitly as part of `extensionData` with a signature or by restructuring the hook signature to include an `originator` field distinct from `sender`.

Until fixed, pool admins should **not** allowlist the router address in `SwapAllowlistExtension`; instead, they must require all allowlisted users to call `pool.swap()` directly.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps for allowlisted users
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...) — pool sees msg.sender = router
  3. Pool calls extension.beforeSwap(router, bob, ...)
  4. Extension checks allowedSwapper[pool][router] → true → passes
  5. bob's swap executes against the curated pool's liquidity

Expected: revert NotAllowedToSwap (bob is not allowlisted)
Actual:   swap succeeds — allowlist is bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
