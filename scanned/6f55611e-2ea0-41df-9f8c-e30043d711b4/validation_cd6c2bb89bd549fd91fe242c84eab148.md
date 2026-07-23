### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which equals the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the end-user's address. Any pool admin who allowlists the router to enable standard router access for legitimate users inadvertently opens the pool to all users, completely defeating the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is set to `msg.sender` from the pool's perspective — i.e., the direct caller of `pool.swap()`. [2](#0-1) 

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), NOT the end-user
    recipient,
    ...
);
``` [3](#0-2) 

When a user swaps through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
``` [4](#0-3) 

The router stores the original `msg.sender` in transient storage for the payment callback, but **does not forward the end-user's identity to the pool's `swap()` call**. The pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Result |
|---|---|
| Do **not** allowlist the router | Legitimate allowlisted users cannot use `MetricOmmSimpleRouter` — core swap flow broken |
| **Allowlist the router** | Any unprivileged user bypasses the allowlist by routing through the router |

There is no configuration that simultaneously allows specific users to use the router and blocks non-allowlisted users from doing the same, because the router is a shared contract and the extension cannot distinguish end-users behind it.

---

### Impact Explanation

The swap allowlist is an admin-configured access-control boundary. When the router is allowlisted (the only way to support the standard user-facing interface for legitimate users), any unprivileged user can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. Pools intended to be restricted to specific participants (e.g., institutional, KYC-gated, or permissioned pools) become fully open to all users. This is an admin-boundary break where an unprivileged path defeats a configured guard with direct swap-access consequences.

---

### Likelihood Explanation

Medium. The pool admin must allowlist the router for the allowlist to be practically usable with the standard periphery. This is the natural and expected configuration for any pool that deploys `SwapAllowlistExtension` alongside `MetricOmmSimpleRouter`. The bypass is then reachable by any unprivileged user with no special preconditions.

---

### Recommendation

The router should embed the original caller's identity in `extensionData` (or a dedicated field) so that `SwapAllowlistExtension` can check the actual end-user rather than the router. Alternatively, `SwapAllowlistExtension` should accept a signed or verified user identity from `extensionData` when `sender` is a known router, falling back to `sender` for direct pool calls. The extension must gate the economically relevant actor, not the intermediary contract.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use the standard interface: `setAllowedToSwap(pool, router, true)`.
4. Bob (non-allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` = `true` → **passes**.
7. Bob's swap executes successfully in a pool he should not have access to.

The allowlist is completely bypassed for all router-mediated swaps once the router is allowlisted, regardless of whether the individual end-user is on the allowlist. [5](#0-4) [6](#0-5)

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
