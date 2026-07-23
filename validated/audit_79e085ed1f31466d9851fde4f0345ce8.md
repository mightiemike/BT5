Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the immediate caller (`sender`) rather than the originating user, allowing any address to bypass per-user swap allowlists via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which `MetricOmmPool.swap()` populates with its own `msg.sender`. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router contract address, not the originating user. A pool admin who allowlists the router to support standard periphery UX inadvertently grants every unprivileged user the ability to bypass the per-user allowlist, rendering the extension's curation guarantee void.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` verbatim as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the value forwarded from `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

Inside `pool.swap()`, `msg.sender` is the router, so `sender` delivered to the extension is the router address. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. Any user who calls the router against a pool that has allowlisted the router will pass the check regardless of whether they are individually allowlisted.

The contrast with `DepositAllowlistExtension` confirms this is a design gap: the deposit extension receives an explicit `owner` parameter (the actual depositor) and checks that, so it is immune to the same router-mediation attack. The swap interface has no equivalent explicit-user parameter.

## Impact Explanation
The `SwapAllowlistExtension` is the sole on-chain mechanism for per-user swap curation on a pool. When the router is allowlisted, the extension's decision (`allowedSwapper[pool][sender]`) evaluates to `true` for every caller of the router, including addresses the pool admin explicitly never allowlisted. The allowlist ceases to function as a curation gate. Any unprivileged user can execute swaps against the pool's liquidity, constituting broken core pool functionality and potential loss of funds for LPs who deposited under the assumption that only curated counterparties could trade against their positions.

## Likelihood Explanation
The trigger requires the pool admin to call `setAllowedToSwap(pool, router, true)`. This is a natural and expected operational step for any curated pool that also wants to support the standard periphery UX (router-mediated swaps for allowlisted users). The admin has no on-chain signal that doing so collapses all per-user distinctions. Once the router is allowlisted, the bypass is reachable by any unprivileged user with no further preconditions, and is repeatable indefinitely.

## Recommendation
The extension must check the economically relevant actor, not the immediate caller. Two options:

1. **Pass the original initiator through `extensionData`**: the router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires a convention between router and extension.
2. **Add an explicit `swapper` parameter to the swap interface** (analogous to `owner` in `addLiquidity`/`beforeAddLiquidity`) that the pool populates from a trusted source, so the extension always receives the original user regardless of routing path. This mirrors the pattern already used correctly in `DepositAllowlistExtension`.

Until fixed, pool admins must not allowlist the router address on pools that rely on `SwapAllowlistExtension` for per-user curation.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable standard router UX for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  - Router calls pool.swap(); msg.sender inside pool = router.
  - _beforeSwap passes sender = router to SwapAllowlistExtension.
  - Extension checks allowedSwapper[pool][router] == true → passes.
  - Attacker's swap executes against the curated pool's liquidity.

Expected: revert NotAllowedToSwap (attacker is not on the allowlist).
Actual:   swap succeeds; allowlist is bypassed.
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
