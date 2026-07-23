Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Allowing Any Caller to Bypass Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of that call is the router contract, not the end user. A pool admin who allowlists the router so that permitted users can trade through it simultaneously opens the pool to every user who can reach the router, completely defeating the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — so `msg.sender` of that pool call is the router contract, not the originating EOA: [4](#0-3) 

For multi-hop `exactInput`, every hop is also called by the router: [5](#0-4) 

The extension therefore sees `sender = router` for every router-mediated swap regardless of which EOA initiated the transaction. A pool admin faces an impossible dilemma: not allowlisting the router blocks all router-mediated swaps for legitimate users; allowlisting the router grants every user access. There is no configuration that simultaneously permits allowlisted users to trade through the router and blocks non-allowlisted users.

## Impact Explanation
Any user not on the allowlist can bypass the curated pool's swap restriction by calling `router.exactInputSingle` or any other router entry point. The router is a public, permissionless contract. The bypass requires no special privilege, no flash loan, and no multi-transaction setup. Unauthorized swaps on a pool designed for specific participants can drain favorable oracle-anchored prices from LP positions, constituting a direct loss of LP assets and a complete failure of the admin-configured access boundary. This matches the **admin-boundary break** allowed impact: an unprivileged caller bypasses a pool admin-configured access control.

## Likelihood Explanation
The router is the primary user-facing interface for the protocol. Any pool admin who deploys a `SwapAllowlistExtension` and wants their allowlisted users to trade normally will allowlist the router — this is the expected operational path. Once the router is allowlisted, the bypass is trivially reachable by any EOA with no prerequisites. The attack is repeatable on every block.

## Recommendation
The extension must gate the originating user, not the intermediary. Two complementary approaches:

1. **Thread the real initiator through the router.** The router already stores the original `msg.sender` in transient storage as the payer via `_setNextCallbackContext`. Expose this initiator as an additional field in the `beforeSwap` call or via a transient slot that extensions can read, so the extension always sees the EOA regardless of the call path.

2. **Redesign the extension to read initiator from trusted transient context.** Have the router write the originating EOA into a known transient slot before calling the pool, and have `SwapAllowlistExtension.beforeSwap` read from that slot (verifying the writer is a trusted router) instead of relying on the `sender` argument.

Until fixed, pools relying on `SwapAllowlistExtension` for access control must not allowlist the router and must document that router-mediated swaps are unsupported.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
  - bob is NOT allowlisted.

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=bob, ...)  →  msg.sender of pool call = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] == true  →  PASSES
  5. Swap executes; bob receives output tokens from the curated pool.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds — allowlist fully bypassed
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
