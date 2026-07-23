Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the router is allowlisted rather than whether the end user is allowlisted. Any user who calls the router on a pool where the router is allowlisted bypasses the curated access control entirely.

## Finding Description
The full call chain is confirmed in production code:

1. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user's address: [1](#0-0) 

2. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

3. `ExtensionCalling._beforeSwap` forwards that same `sender` value unchanged to the extension: [3](#0-2) 

4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — never the end user: [4](#0-3) 

This creates an impossible choice for pool admins: allowlisting the router (to give approved users normal UX) grants every user on earth the ability to swap, defeating the allowlist entirely. Not allowlisting the router forces individually approved users to call the pool directly. `DepositAllowlistExtension` avoids this flaw by checking the `owner` argument (the position owner explicitly passed by the caller) rather than `sender` (the immediate caller): [5](#0-4) 

## Impact Explanation
A curated pool relying on `SwapAllowlistExtension` to restrict trading to approved counterparties (e.g., KYC'd or protocol-internal users) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The unauthorized swapper can drain LP-owned token reserves at oracle-quoted prices, causing direct loss of LP principal. This constitutes a critical/high direct loss of user principal — the allowlist, the only on-chain mechanism preventing unauthorized swaps, provides zero protection once the router is allowlisted.

## Likelihood Explanation
The router is the primary user-facing swap entry point. Any pool admin who wants allowlisted users to have standard UX (deadline, slippage, multi-hop) will allowlist the router. The moment they do, the allowlist is void. The attacker requires no special privilege, no flash loan, and no oracle manipulation — a single call to `exactInputSingle` suffices. The condition is highly likely to occur in any real deployment.

## Recommendation
Pass the originating user through the call chain rather than the immediate caller. Two concrete options:

1. **Router forwards the real sender**: Add a `sender` field to swap parameters that the router populates with `msg.sender` and the pool passes to extensions instead of its own `msg.sender`.
2. **Extension reads from transient storage**: The router writes the real user into a transient slot before calling the pool; the extension reads it. This mirrors the pattern already used for the callback payer in `MetricOmmSwapRouterBase`.

Either way, `SwapAllowlistExtension.beforeSwap` must compare against the end user's address, not the address of whatever contract called `pool.swap`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin allowlists router so users can swap
  allowedSwapper[pool][alice]  = true   // alice is individually approved
  allowedSwapper[pool][bob]    = false  // bob is NOT approved

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → pool.swap(msg.sender = router)
    → _beforeSwap(sender = router)
    → SwapAllowlistExtension.beforeSwap(sender = router)
    → allowedSwapper[pool][router] == true  → passes
    → bob's swap executes, draining LP reserves

Result:
  bob, a disallowed swapper, successfully trades on a curated pool.
  The allowlist provided zero protection.
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
