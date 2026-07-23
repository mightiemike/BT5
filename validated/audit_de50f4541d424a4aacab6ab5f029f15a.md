Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is the direct `msg.sender` of `pool.swap()` — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]`. If the pool admin allowlists the router (a required step for any router-mediated swap to succeed), every user — including those the admin intended to block — can bypass the allowlist by calling the router instead of the pool directly.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (enforced by `onlyPool`) and `sender` is the value the pool passes — which is `msg.sender` of the `pool.swap()` call itself:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient, ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly with `""` as `extensionData`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
```

The pool's `msg.sender` is the router, so `sender = address(router)` is what the extension sees. The extension evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router, the check passes for every user who calls through the router, regardless of whether that user is individually allowlisted.

Critically, the `beforeSwap` function discards the `bytes calldata` extensionData parameter entirely (it is unnamed), so there is no existing mechanism to pass the original caller's identity through extensionData. The router also passes `""` as the first `extensionData` argument (the pool-level one), and `params.extensionData` is user-controlled, meaning a malicious user could supply arbitrary bytes — but the extension ignores them anyway.

The pool admin faces an impossible choice:
- **Allowlist the router** → any user bypasses the allowlist via the router.
- **Do not allowlist the router** → no router-mediated swaps work at all.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd market makers, whitelisted protocols) loses that protection entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting the restricted pool and execute swaps that the allowlist was designed to prevent. Because the pool is an oracle-driven market maker, unauthorized swaps extract value from LP positions at oracle-quoted prices, directly reducing LP principal. This constitutes a broken core pool invariant with direct loss of LP assets — a High severity impact under Sherlock contest thresholds.

## Likelihood Explanation

The trigger is fully unprivileged: any EOA or contract can call the public router. The only precondition is that the pool admin has allowlisted the router — a step they must take if they want any router-mediated swaps to work. Pools that intend to support both an allowlist and router access are the exact target. The attacker needs no special role, no flash loan, and no oracle manipulation; a single `exactInputSingle` call suffices.

## Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. Two complementary fixes:

1. **Pass the original user through `extensionData`.** The router should populate `extensionData` with `abi.encode(msg.sender)`, and `SwapAllowlistExtension.beforeSwap` should decode and verify that address when `extensionData` is non-empty, falling back to `sender` only when `extensionData` is empty.

2. **Pool-level transient storage for the original initiator.** The pool could expose the original initiator through transient storage, and the extension reads it instead of the `sender` argument.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension as BEFORE_SWAP extension.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowed for router swaps
  4. LP adds liquidity to the pool.

Attack (bob is NOT allowlisted):
  5. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: pool, zeroForOne: true, amountIn: X, recipient: bob, ...
     })
  6. Router calls pool.swap(bob, true, X, ...) with msg.sender = router.
  7. Pool calls extension.beforeSwap(router, bob, ...).
  8. Extension checks allowedSwapper[pool][router] == true → passes.
  9. Swap executes; bob receives token1 at oracle price.
     LP position is reduced without bob being on the allowlist.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
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
