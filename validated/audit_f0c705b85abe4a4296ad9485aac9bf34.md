Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the end user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. If the pool admin allowlists the router so that allowlisted users can reach the pool via the router, every unprivileged user can bypass the allowlist by routing through the same router, completely nullifying the pool's access control.

## Finding Description

**Root cause — actor-binding mismatch in the extension**

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool via `CallExtension.callExtension`) and `sender` is the first argument forwarded by the pool.

**How the pool sets `sender`**

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value verbatim as the first argument to every extension: [3](#0-2) 

**How the router calls the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
``` [4](#0-3) 

The original end-user address (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the payment callback; it is never forwarded to the pool or to any extension. The same pattern applies to `exactInput` (L103-112) and `exactOutputSingle` (L135-137). [5](#0-4) 

**The impossible choice forced on the pool admin**

The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. This forces the pool admin into a binary:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken UX |
| **Allowlist the router** | Every user, allowlisted or not, can bypass the gate by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

## Impact Explanation
Once the pool admin allowlists the router (the natural step to let allowlisted users trade via the router), any unprivileged user can bypass the swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, or `exactOutputSingle`. The curated pool's access control is completely nullified: unauthorized users can trade freely at oracle-anchored prices, defeating the pool's curation guarantee and exposing LP assets to unauthorized counterparties. This constitutes a broken core pool functionality causing potential loss of funds and an admin-boundary break where an unprivileged path bypasses the pool's access control.

## Likelihood Explanation
The trigger is the pool admin allowlisting the router — a natural, expected administrative action for any pool that intends to support the standard periphery. The admin has no on-chain signal that doing so opens the gate to all users; the allowlist storage and the extension logic give no indication of the actor-binding mismatch. The bypass itself requires no special privilege: any EOA can call the public router functions.

## Recommendation
The `SwapAllowlistExtension` must gate on the economic actor (the end user), not on the immediate caller of `pool.swap()`. Two compatible approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. The pool admin must configure the extension to trust the router as a forwarder.
2. **Add an explicit `swapper` parameter to `pool.swap()`**: The pool accepts a `swapper` address distinct from `msg.sender`, validates that `msg.sender` is an approved forwarder, and passes `swapper` to extensions. This is the cleanest fix but requires a core interface change.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Charlie (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` — `msg.sender` inside the pool is the router address.
6. The pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → no revert.
7. Charlie's swap executes at oracle price on the curated pool, bypassing the allowlist entirely.

The exact wrong value is the `sender` argument passed to `beforeSwap`: it is the router address (`allowedSwapper[pool][router] == true`) instead of Charlie's address (`allowedSwapper[pool][charlie] == false`), causing the extension to return `IMetricOmmExtensions.beforeSwap.selector` instead of reverting with `NotAllowedToSwap`. [6](#0-5) [2](#0-1) [7](#0-6)

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
