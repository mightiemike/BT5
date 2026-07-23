Audit Report

## Title
SwapAllowlistExtension Bypassed via Router: `sender` Identity Mismatch Allows Unauthorized Swaps - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the immediate caller of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension receives `sender = address(router)` instead of the actual user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user, nullifying the per-user allowlist entirely.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and dispatches it to each configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` as `msg.sender`: [4](#0-3) 

The router does not forward the original `msg.sender` (the actual user) into `extensionData` or any other field visible to the extension. The pool admin faces an impossible choice:

- **Option A**: Allowlist only specific users (not the router) → those users cannot use the router; their router-mediated swaps revert with `NotAllowedToSwap` because the extension sees `sender = router`, which is not allowlisted.
- **Option B**: Allowlist the router address → every user on the network can call the router and bypass the per-user allowlist, because the extension sees `sender = router` which is allowlisted.

By contrast, `DepositAllowlistExtension` does not share this flaw — it correctly gates on `owner` (the LP position holder), which is explicitly passed through the call chain and is not overwritten by the router: [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a KYC-gated pool, a private institutional pool, or a pool with favorable oracle-anchored pricing reserved for specific counterparties) can be fully opened to any user by routing through the public `MetricOmmSimpleRouter`. Any user who calls `exactInputSingle`, `exactInput`, or `exactOutputSingle` on the router against such a pool will have their swap processed as if the router itself is the swapper. If the router is allowlisted (the only way to let legitimate users use the router), the allowlist is nullified. Unauthorized users can trade at the pool's oracle-anchored prices, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade against them. This constitutes a broken core pool functionality causing loss of funds and a direct bypass of an admin-configured access control boundary.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public entry point for swaps. Any user who discovers a restricted pool with favorable pricing has a direct, permissionless path to bypass the allowlist by calling the router. No privileged access, flash loan, or special setup is required. The trigger is a standard `exactInputSingle` call with the restricted pool as the target. The attack is repeatable and requires no special on-chain state beyond the router being allowlisted.

## Recommendation

The extension must resolve the true initiator of the swap, not the immediate caller of `pool.swap()`. Two concrete approaches:

1. **Pass the original initiator explicitly via `extensionData`**: Require the router to encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify that value instead of `sender`.
2. **Redesign the allowlist to gate on an invariant identity**: Use a signed or verified user identity in `extensionData` that the extension validates, rather than relying on `sender` which is overwritten by the router.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary to allow any router-mediated swap for legitimate users)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls router.exactInputSingle({pool: restrictedPool, ...})
  - router calls pool.swap(recipient, ...) with msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for attacker
  - Attacker trades at oracle-anchored prices in a pool intended to be restricted

Alternative (shows the dilemma):
  - Pool admin does NOT allowlist the router
  - Legitimate allowlisted user calls router.exactInputSingle(...)
  - Extension checks allowedSwapper[pool][router] → false
  - Swap reverts: legitimate user cannot use the router at all
```

Both outcomes break the invariant that the allowlist gates the economically relevant swapper.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
  }
```

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
