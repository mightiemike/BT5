Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates on Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Restriction — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable router-based swaps inadvertently opens the gate to every user, including those not on the per-user allowlist, completely defeating the extension's access-control purpose.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The allowlist mapping is keyed `allowedSwapper[pool][swapper]`: [5](#0-4) 

For router-originated swaps, the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces a binary choice with no middle ground: allowlisting the router opens swaps to every user; not allowlisting it blocks all router-based swaps even for allowlisted users.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker executes swaps against the pool's liquidity without authorization, draining LP value at oracle-derived prices the pool admin intended to expose only to trusted parties. This constitutes a broken core pool invariant (the allowlist extension's access control is rendered ineffective) and a direct loss of LP principal. Severity: High.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point in the periphery. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. Any user who discovers the allowlist can trivially route through the router instead of calling `pool.swap()` directly. The condition is trivially reachable and repeatable.

## Recommendation

The root cause is that the pool only passes the direct caller (`msg.sender`) as `sender` to extensions, with no separate field for the originating end-user. Two concrete fixes:

1. **Encode the real swapper in `extensionData`**: have the router encode `msg.sender` (the end-user) into `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and verify it. This requires the extension to trust the router's encoding.
2. **Add an `originator` field to the swap call chain**: have the pool pass a distinct `originator` / `payer` address through `_beforeSwap` (analogous to how `addLiquidity` separates `sender` from `owner`), populated by the router from its own `msg.sender`.

Until fixed, `SwapAllowlistExtension` must document that it gates the direct caller of `pool.swap()`, not the end-user, and pool admins must not rely on it for per-user access control when the router is in use.

## Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension as a beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow allowlisted users to trade via the router.
3. Attacker (address NOT in allowedSwapper[pool]) calls:
       MetricOmmSimpleRouter.exactInputSingle(pool, tokenIn, tokenOut, amountIn, ...)
4. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData).
5. Pool calls _beforeSwap(msg.sender=router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...).
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Swap executes; attacker receives output tokens.
   Per-user allowlist is completely bypassed.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
