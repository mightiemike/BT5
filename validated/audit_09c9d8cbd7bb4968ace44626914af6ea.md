Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the pool's `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the immediate caller, so the extension evaluates the router's address against the allowlist rather than the actual end-user's address. If a pool admin allowlists the router to enable router-mediated swaps, every unpermissioned user can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The `recipient` parameter, which carries the actual end-user address, is silently discarded by the extension.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the allowlist check against `sender`:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct), and `sender` is the address the pool received as its own `msg.sender` when `swap()` was called. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap() — the router, not the end user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` then forwards that same `sender` value directly to the extension:

```solidity
// ExtensionCalling.sol L149-177
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(recipient=user, ...)` with `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,   // ← actual user address
        params.zeroForOne,
        ...
    );
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The `recipient` parameter — which carries the actual end-user address — is the unnamed, discarded second parameter in `beforeSwap`. A pool admin who wants to allow router-mediated swaps for their allowlisted users will naturally call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every user — including those explicitly not on the allowlist — can bypass the gate by routing through `MetricOmmSimpleRouter`.

## Impact Explanation

High. A curated pool using `SwapAllowlistExtension` to restrict trading to KYC'd, institutional, or otherwise vetted counterparties loses all access-control enforcement the moment the router is allowlisted. Any unpermissioned user can execute swaps at the pool's oracle-anchored bid/ask prices, causing direct loss of LP principal. The pool's entire curation policy is silently nullified. This matches the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" allowed impact categories.

## Likelihood Explanation

Medium. Allowlisting the router is the natural, expected admin action to make a curated pool usable through the standard periphery. The admin has no on-chain signal that doing so opens the gate to all users. The bypass requires no special privileges, no flash loan, and no contract deployment — any EOA can call `router.exactInputSingle`. The precondition (router allowlisted) is the expected operational state for any pool that intends to support router-mediated swaps.

## Recommendation

The extension must gate on the economically relevant actor. Two viable options:

1. **Check `recipient` instead of `sender`** when `sender` is a known router. Because the router passes the actual user as `recipient`, the extension can check `allowedSwapper[pool][recipient]` when `sender` is a trusted router address.
2. **Check both `sender` and `recipient`**: require that at least one of them is allowlisted, so direct swaps and router-mediated swaps are both covered by the same policy.
3. **Require direct pool calls only**: document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` has code (i.e., is a contract).

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin: setAllowedToSwap(pool, router, true)      // enable router-mediated swaps
3. Admin: setAllowedToSwap(pool, userB, false)       // userB is NOT allowlisted (default)
4. userB calls router.exactInputSingle({pool, tokenIn, tokenOut, recipient: userB, ...})
5. Router calls pool.swap(recipient=userB, ...)      // msg.sender = router
6. Pool calls _beforeSwap(sender=router, recipient=userB, ...)
7. Extension checks allowedSwapper[pool][router] == true  → passes
8. userB's swap executes on the curated pool despite not being allowlisted.
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
