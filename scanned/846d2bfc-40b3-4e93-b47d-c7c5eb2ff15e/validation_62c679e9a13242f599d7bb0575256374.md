### Title
`SwapAllowlistExtension` gates on the router's address instead of the end-user's address, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of the pool call, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who allowlists the router address (a natural action to enable router-mediated swaps) inadvertently opens the gate to every user, defeating the allowlist entirely.

---

### Finding Description

**Pool `swap` passes `msg.sender` (the router) as `sender` to the extension:**

In `MetricOmmPool.swap`:
```solidity
_beforeSwap(
  msg.sender,   // = router when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim:
```solidity
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)   // sender = router
)
```

**`SwapAllowlistExtension.beforeSwap` checks the wrong identity:**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct). `sender` is whoever called `pool.swap()` — the router, not the end-user. The check resolves to `allowedSwapper[pool][router]`.

**The router does not forward the end-user's identity:**

`MetricOmmSimpleRouter.exactInputSingle` calls:
```solidity
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  params.zeroForOne,
  ...
  params.extensionData
);
```

There is no mechanism to pass `msg.sender` (the actual user) through to the extension. The pool only sees the router as the caller.

**The discrepancy:**

| Path | Identity checked by extension |
|---|---|
| Direct `pool.swap()` by user | `allowedSwapper[pool][user]` ✓ |
| Via `MetricOmmSimpleRouter` | `allowedSwapper[pool][router]` ✗ |

This is the exact same class of bug as the Wise Lending M-04: a guard uses a different value for its check depending on the execution path, creating a discrepancy that allows the guard to be bypassed.

---

### Impact Explanation

**Bypass path (Critical/High):** A pool admin who wants to allow router-mediated swaps for their allowlisted users will naturally allowlist the router address: `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, *any* user — including those explicitly not on the allowlist — can bypass the swap gate by routing through `MetricOmmSimpleRouter`. The allowlist provides zero protection against router-mediated swaps.

**Broken functionality path (Medium):** If the pool admin only allowlists specific user addresses (not the router), those users cannot use the router at all, because the extension checks the router's address and finds it not allowlisted. Core swap functionality is broken for the intended users.

Both impacts are fund-relevant: the bypass allows unauthorized parties to trade in a restricted pool (e.g., a KYC-gated or institutional pool), and the broken path prevents legitimate LPs from executing swaps.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is a natural and expected administrative action — a pool admin who deploys a `SwapAllowlistExtension` and also wants their users to use the official router will allowlist the router. The admin has no on-chain signal that doing so opens the gate to all users. The trigger is a single `setAllowedToSwap(pool, router, true)` call by a semi-trusted pool admin acting in good faith.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the actual end-user, not the immediate caller of `pool.swap()`. Two options:

1. **Pass the end-user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is fragile.

2. **Check `recipient` instead of `sender`**: The recipient is the address that receives swap output, which is typically the end-user. However, this is also not always the initiating user.

3. **Preferred — check both `sender` and a user field from `extensionData`**: The extension decodes an explicit `swapper` address from `extensionData` (set by the router to `msg.sender`) and checks that. The pool admin allowlists user addresses, not the router. The router always injects the real user.

The simplest safe fix is to have `MetricOmmSimpleRouter` encode the real `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when present, falling back to `sender` for direct pool calls.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on the `beforeSwap` hook.
2. Pool admin allowlists `userA`: `setAllowedToSwap(pool, userA, true)`.
3. Pool admin also allowlists the router (to enable router-mediated swaps): `setAllowedToSwap(pool, router, true)`.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(userB, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, userB, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `userB` successfully swaps in a pool they are not allowlisted for.

The allowlist is fully bypassed. Any user who routes through `MetricOmmSimpleRouter` is indistinguishable from the router itself as far as the extension is concerned. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
