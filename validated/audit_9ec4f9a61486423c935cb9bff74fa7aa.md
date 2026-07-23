Audit Report

## Title
SwapAllowlistExtension gates on router address instead of originating user, allowing full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the pool's `msg.sender` at swap time. When any user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`, so the extension checks the router's address — not the actual user. Any pool admin who allowlists the router (required for any router-mediated swap) inadvertently grants every unprivileged user the ability to bypass the per-user restriction.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct), and `sender` is the argument forwarded by `ExtensionCalling._beforeSwap`: [2](#0-1) 

The pool sets `sender = msg.sender` at the `swap()` entry point. When `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()`, the pool's `msg.sender` is the router contract: [3](#0-2) 

The router does not embed the originating user's address in `extensionData` or any other field the extension can verify. The extension has no mechanism to recover the real user identity — it only receives `sender = router_address`. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

## Impact Explanation

A pool admin deploying `SwapAllowlistExtension` to restrict swaps to trusted counterparties (KYC'd users, whitelisted market makers, protocol-controlled addresses) loses that protection entirely once the public router is allowlisted. Any unprivileged address can execute swaps against the pool at live oracle prices, exposing LPs to adverse selection, toxic flow, or regulatory non-compliance. This is a direct admin-boundary break with LP-fund-impacting consequences meeting the "admin-boundary break" and "broken core pool functionality causing loss of funds" criteria.

## Likelihood Explanation

The trigger is fully unprivileged. The only precondition is that the pool admin has allowlisted the router — which is the necessary and sufficient condition for any legitimate router-based swap to work on the pool. The `MetricOmmSimpleRouter` is a public, permissionless contract with no access control on its entry points. [5](#0-4) 

No special role, flash loan, or callback is required. The pool admin faces an impossible choice: allowlist the router (bypass for all users) or block the router (no router swaps at all).

## Recommendation

The router should embed the originating `msg.sender` in `extensionData` (e.g., ABI-encoded as the first word), and `SwapAllowlistExtension.beforeSwap` should decode and check that field when `sender` is a known router. Alternatively, the pool interface should be extended with a separate `origin` argument that the router populates with the real user address, and extensions should gate on `origin` rather than `sender`.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required for any router swap.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)` → extension receives `sender = router`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → passes.
8. Attacker's swap executes against the pool despite never being individually allowlisted. [6](#0-5) [7](#0-6)

### Citations

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
