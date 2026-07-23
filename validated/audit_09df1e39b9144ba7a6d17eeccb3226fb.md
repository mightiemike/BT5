Audit Report

## Title
SwapAllowlistExtension Gates the Router Contract Instead of the Originating EOA, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool populates with its own `msg.sender` — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that direct caller is the router contract, not the originating EOA. Any pool that allowlists the router to enable standard periphery usage simultaneously grants every EOA — including those the admin intended to block — unrestricted swap access.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool: [1](#0-0) 

`MetricOmmPool.swap` populates that `sender` argument with its own `msg.sender` — the direct caller of `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [3](#0-2) 

The same pattern holds for `exactInput` (all forward hops): [4](#0-3) 

And for `exactOutputSingle` and the recursive hops inside `_exactOutputIterateCallback`: [5](#0-4) [6](#0-5) 

Therefore, the extension always sees `sender = router_address` for every router-mediated swap, regardless of which EOA initiated the transaction. This creates two mutually exclusive broken states:

1. **Allowlist bypass:** The pool admin allowlists the router (the only way to permit standard periphery usage). Any EOA — including those the admin intended to block — can now swap freely by calling the router.
2. **Broken core functionality:** The pool admin allowlists specific EOAs instead. Those EOAs cannot use the router at all; they must call `pool.swap()` directly.

By contrast, `DepositAllowlistExtension` does not share this flaw because it checks `owner`, which is an explicit argument supplied by the caller rather than the EVM-level `msg.sender`: [7](#0-6) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to approved counterparties loses that protection entirely the moment the router is allowlisted. Any unpermissioned EOA can execute swaps against the pool's LP assets by routing through `MetricOmmSimpleRouter`, directly violating the pool's curation policy and exposing LP funds to unauthorized trading activity. This constitutes broken core pool functionality — the allowlist guard silently fails open on the standard periphery path — and represents a direct loss of curation control over LP assets.

## Likelihood Explanation
The router is the primary user-facing entry point for swaps. Any pool that (a) deploys `SwapAllowlistExtension` and (b) wants to support normal periphery usage must allowlist the router, immediately enabling the bypass. The attacker requires no special privileges, no malicious setup, and no non-standard tokens — only a call to the public `exactInputSingle` or `exactInput` router function. The condition is trivially reachable by any EOA.

## Recommendation
The extension must gate the originating EOA, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the original caller through the router.** The router already stores `msg.sender` in transient storage for the callback payer via `_setNextCallbackContext`. The extension could read the originating sender from a well-known transient slot populated by the router, or the pool could forward a separate `originalSender` field to extensions.

2. **Check `recipient` instead of `sender`.** The `recipient` is the address that receives output tokens and is the economically meaningful actor. It is already forwarded correctly through the router (`params.recipient`). The extension would then check `allowedSwapper[pool][recipient]`.

The invariant that must hold is: the identity checked by the allowlist must be the same actor the pool admin intended to gate, regardless of which supported public entrypoint reaches the pool.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` — the only way to enable router-mediated swaps.
3. Admin does **not** allowlist `mallory` (an unauthorized EOA).
4. `mallory` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → passes.
8. `mallory` successfully swaps against the curated pool, bypassing the allowlist entirely.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-40)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```
