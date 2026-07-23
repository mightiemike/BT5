Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter`: Any Unprivileged User Can Swap in Gated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool populates with its own `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the router becomes the pool's `msg.sender`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router (required for any allowlisted user to trade through it), every address on-chain can bypass the allowlist via the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. `MetricOmmPool.swap` passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The router is `msg.sender` of the pool call, so the extension checks `allowedSwapper[pool][router]`. The pool admin faces an irresolvable dilemma: not allowlisting the router blocks all allowlisted users from using it; allowlisting the router opens the gate to every address on-chain. No configuration simultaneously permits allowlisted users to trade through the router while blocking non-allowlisted users.

## Impact Explanation
High. `SwapAllowlistExtension` is the primary mechanism for pools restricting trading to specific counterparties (KYC'd users, institutional LPs, whitelisted market makers). Once the router is allowlisted, any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and trade against the pool's liquidity without restriction. LP funds are directly exposed to counterparties the pool admin explicitly intended to exclude. The pool's core access-control invariant is fully broken for all router-mediated swap paths.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is a public, permissionless contract. No special privilege, timing window, or admin error is required beyond the router being allowlisted (which is a necessary precondition for any allowlisted user to use the router). Any user who discovers the pool uses `SwapAllowlistExtension` can immediately route through `MetricOmmSimpleRouter` to bypass the gate. The bypass is structural and repeatable.

## Recommendation
The extension must gate the originating user, not the intermediary contract. The preferred fix is to have the router encode `msg.sender` into `extensionData` in a standardized field, and have the extension decode and verify it — falling back to `sender` only when `sender` is not a known router. Alternatively, document clearly that `SwapAllowlistExtension` only gates direct `pool.swap()` calls and is ineffective for router-mediated swaps, so pool admins do not deploy it under the false assumption that it covers all swap paths.

## Proof of Concept
```
Setup:
  - Pool P configured with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, alice, true)
  - Pool admin calls E.setAllowedToSwap(P, router, true)  ← required for alice to use the router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient=bob, ...) — router is msg.sender of the pool
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[P][router] → true
  5. Swap proceeds; bob receives output tokens from the pool

Result:
  - bob, an explicitly non-allowlisted address, successfully swaps against the restricted pool
  - The allowlist guard is fully bypassed
  - LP funds are exposed to an unauthorized counterparty
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
