Audit Report

## Title
SwapAllowlistExtension Gates Direct Caller of `pool.swap()` Instead of End User, Enabling Full Allowlist Bypass via Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the `pool.swap()` call — the router address, not the end user. A pool admin who allowlists the router so that approved users can access the standard periphery inadvertently grants every user on-chain the ability to pass the allowlist check, because `MetricOmmSimpleRouter` is a public, permissionless contract with no per-user access control.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`. In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the first argument:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` passes this value unchanged into the ABI-encoded call to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same pattern applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`. [5](#0-4) 

The extension's NatSpec documents the intent as "Gates `swap` by swapper address, per pool": [6](#0-5) 

The mismatch between documented intent (gate by actual swapper identity) and implementation (gate by direct caller of `pool.swap()`) means that allowlisting the router — a necessary step for approved users to use the standard periphery — opens the pool to all users. No existing guard in the extension or the router prevents this.

## Impact Explanation

Any unprivileged user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. The pool was configured to restrict swaps to specific addresses; the bypass allows unauthorized traders to execute swaps at oracle-derived prices, exposing LP principal and fees to unauthorized trading. This constitutes broken core pool functionality (the allowlist guard fails open) and direct loss of user funds above Sherlock thresholds.

## Likelihood Explanation

Pool admins are expected to allowlist the router so that their approved users can access the standard periphery. The extension is documented as gating by swapper address, implying it gates by actual swapper identity. The mismatch is non-obvious. Once the router is allowlisted (a natural and expected admin action), the bypass is immediately available to any on-chain caller with no special privileges, capital, or timing requirements. It is repeatable indefinitely.

## Recommendation

1. **Short-term:** Document explicitly that `allowedSwapper` gates the direct caller of `pool.swap()`, not the end user, and that allowlisting the router opens the pool to all users.
2. **Structural fix:** Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and check that address instead of `sender`. This requires router cooperation and a defined encoding convention.
3. **Alternative:** Introduce a two-tier allowlist — one for trusted intermediary contracts (routers) and one for end users — so that allowlisting the router does not bypass per-user gating.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.

2. Pool admin allowlists user A (the intended authorized trader):
   swapExtension.setAllowedToSwap(pool, userA, true)

3. Pool admin allowlists the router so userA can use the standard periphery:
   swapExtension.setAllowedToSwap(pool, router, true)

4. User B (not allowlisted) calls:
   router.exactInputSingle({pool: pool, tokenIn: token0, ...})

5. Router calls pool.swap(...) — pool sees msg.sender = router.
   Extension evaluates: allowedSwapper[pool][router] → true → no revert.

6. User B's swap executes on the curated pool, bypassing the allowlist.
   LP funds are exposed to unauthorized trading.
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37: the check keys on `sender` (the direct caller of `pool.swap()`), which is the router for all router-mediated swaps, rather than the economically relevant end user. [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
