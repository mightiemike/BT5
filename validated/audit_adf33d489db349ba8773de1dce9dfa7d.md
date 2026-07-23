Audit Report

## Title
SwapAllowlistExtension Bypassed via MetricOmmSimpleRouter — Any User Can Swap on Allowlist-Restricted Pools - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the identity of the immediate caller of `pool.swap` (`sender`), not the original end-user. When `MetricOmmSimpleRouter` is used, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`. If the pool admin allowlists the router (a natural operational step), every user — including non-allowlisted ones — can bypass the per-user allowlist by routing through the router, rendering the access control inoperative.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol lines 230-240
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient,
  ...
  extensionData
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol lines 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` (lines 71–86) stores the original `msg.sender` only in transient callback context for payment purposes (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), but passes no original-caller identity to the pool's extension system — only `params.recipient` and the router's own address as `msg.sender`. The extension has no mechanism to recover the original caller.

**Attack path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` wired into `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)` to let allowlisted users reach the pool through the standard periphery router.
3. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Bob's identity is never checked; the allowlist is fully bypassed.

## Impact Explanation
The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade against a pool's LP liquidity. Once the router is allowlisted, the guard is inoperative for all router-mediated swaps. LPs who deployed capital into a restricted pool (e.g., to serve only KYC'd counterparties or avoid informed-trader flow) are exposed to unrestricted swap volume from any address. This constitutes broken core pool functionality with potential direct loss of LP assets through unintended adverse selection — matching the "Broken core pool functionality causing loss of funds" allowed impact.

## Likelihood Explanation
Allowlisting the router is the expected operational step for any pool that wants to support both an allowlist and the standard periphery router. The pool admin has no in-protocol warning that doing so nullifies per-user restrictions. The bypass is reachable by any public user with no special privileges, no malicious setup, and no non-standard tokens. It is repeatable on every swap.

## Recommendation
The extension must verify the **original end-user**, not the intermediate router. Two viable approaches:

1. **Router forwards caller identity in `extensionData`**: `MetricOmmSimpleRouter` encodes `msg.sender` into the `extensionData` bytes it passes to the pool; `SwapAllowlistExtension` decodes and checks that address when `sender` is a known router.
2. **Maintain a router registry in the extension**: `SwapAllowlistExtension` maintains a set of known router addresses; when `sender` is a known router, it decodes the original caller from `extensionData` and checks that address instead.

Additionally, document clearly that allowlisting a router address opens the pool to all router callers, not just individually allowlisted users.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension wired to beforeSwap.
2. Pool admin:
     extension.setAllowedToSwap(pool, alice, true)
     extension.setAllowedToSwap(pool, router, true)  // intended to let alice use router
3. Bob (not allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: bob, ...})
4. Router calls pool.swap(bob, ...) with msg.sender = router
5. Pool calls extension.beforeSwap(sender=router, ...)
   → allowedSwapper[pool][router] == true → passes
6. Bob's swap executes against LP liquidity despite not being on the allowlist.
```

Verified against production code:
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
