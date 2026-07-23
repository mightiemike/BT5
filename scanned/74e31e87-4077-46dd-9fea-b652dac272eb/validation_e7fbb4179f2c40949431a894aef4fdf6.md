### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool level is the router contract, not the end user. If the pool admin allowlists the router address (a natural action to enable router-mediated swaps for their curated users), every unprivileged user can bypass the individual allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (enforced by `onlyPool`). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)  // sender = msg.sender of pool.swap()
    )
);
```

And in `MetricOmmPool.swap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- the router, not the end user
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

So `sender` arriving at the extension is always the router address, regardless of which end user initiated the call. The extension cannot distinguish between user A and user B if both go through the same router.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` directly, making the router the `sender` in every case.

### Impact Explanation

A pool admin who wants to allow their allowlisted users to access the pool via the router will add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any** unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and the extension will pass — because the check is `allowedSwapper[pool][router]`, which is `true`. The individual per-user allowlist entries become irrelevant for router-mediated swaps.

The broken invariant: *a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it* (per the audit's own validation focus). The extension enforces the policy only for direct pool calls; router-mediated calls collapse all users into a single identity (the router).

### Likelihood Explanation

The pool admin has a legitimate reason to allowlist the router: their allowlisted users need a better UX than calling the pool directly. The admin allowlists the router believing it is a trusted periphery contract that will enforce the allowlist downstream — but the router performs no such check. This is a realistic misconfiguration, not a malicious setup assumption. Likelihood is **Medium**.

### Recommendation

The extension should check the economically relevant actor — the end user — rather than the immediate pool caller. Two approaches:

1. **Pass the original `msg.sender` through the router as an extra field in `extensionData`** and have the extension decode and verify it. This requires a convention between the router and the extension.

2. **Check `sender` (the router) AND require the router to attest the real user** via a signed or transient-storage-backed mechanism, similar to how the router already stores the payer in transient storage (`_setNextCallbackContext` stores `msg.sender` as the payer).

The simplest safe fix is to document that the router address must never be added to the swap allowlist, and to add a factory-level guard that prevents the router from being registered as an allowlisted swapper on curated pools.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists only `user1`: `swapExtension.setAllowedToSwap(pool, user1, true)`.
3. Pool admin also allowlists the router so `user1` can use it: `swapExtension.setAllowedToSwap(pool, router, true)`.
4. `user2` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
5. The pool calls `_beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap proceeds.
6. `user2` successfully trades in a pool they were never individually authorized to access.

The extension's `beforeSwap` at [1](#0-0)  checks `sender` (the immediate pool caller), which is set to `msg.sender` of the pool's `swap` call at [2](#0-1)  — always the router when users go through [3](#0-2) . The `ExtensionCalling._beforeSwap` dispatcher forwards `sender` verbatim at [4](#0-3) , with no mechanism to recover the original end user's address.

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
