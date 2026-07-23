Audit Report

## Title
`SwapAllowlistExtension` Gates on Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, but `sender` is populated from `msg.sender` of `MetricOmmPool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Any pool that allowlists the router to support legitimate router-mediated swaps simultaneously opens an unrestricted bypass for every address not on the allowlist.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value as `sender` to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user:

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

`msg.sender` of `pool.swap()` is the router, so `sender` passed to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. No existing guard in the extension, pool, or router recovers the originating user identity.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd addresses or institutional counterparties loses that guarantee entirely once the router is allowlisted. Any address not on the allowlist can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and execute swaps against the pool at oracle-derived prices. This constitutes a complete failure of the pool's access-control invariant and a direct loss of LP principal, matching the "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" allowed impacts.

## Likelihood Explanation
The router is the primary user-facing entrypoint for swaps. Any pool intending to support router-mediated swaps for its allowlisted users must allowlist the router, which immediately opens the bypass to all users. The attack requires only a standard public call to the router with no privileged access, no special setup, and no non-standard tokens. The condition is met by default for any allowlist-gated pool that supports the router.

## Recommendation
Pass the originating user through the swap path so the extension can gate on the actual economic actor:

1. **Extend `pool.swap()` signature** to accept an explicit `swapper` address (analogous to how `addLiquidity` separates `msg.sender` payer from `owner`), and pass that through `_beforeSwap` as the identity to gate. The router would pass `msg.sender` (the end user) as `swapper`.
2. **Authenticated forwarded-sender pattern** (ERC-2771-style): embed the originating user in `extensionData` with a router-signed attestation, and verify it inside the extension. This avoids changing the pool interface but requires extension-level trust in the router.

Until fixed, pools relying on `SwapAllowlistExtension` should not allowlist the router and should document that router-mediated swaps are unsupported.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap
  - Pool admin allowlists the router: setAllowedToSwap(pool, router, true)
  - Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → msg.sender of pool.swap() = router
  3. Pool calls _beforeSwap(sender=router, ...)
     [metric-core/contracts/MetricOmmPool.sol L230-240]
  4. ExtensionCalling._beforeSwap encodes (sender=router, ...) and dispatches
     [metric-core/contracts/ExtensionCalling.sol L160-176]
  5. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
     [metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37]
  6. Swap executes; attacker receives output tokens

Result: attacker, explicitly excluded from the allowlist, successfully swaps
        against the curated pool, bypassing the intended access control.

Foundry test outline:
  - deployPool with SwapAllowlistExtension
  - vm.prank(poolAdmin); extension.setAllowedToSwap(pool, router, true)
  - assert extension.isAllowedToSwap(pool, attacker) == false
  - vm.prank(attacker); router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
  - assert swap succeeded (no revert)
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
