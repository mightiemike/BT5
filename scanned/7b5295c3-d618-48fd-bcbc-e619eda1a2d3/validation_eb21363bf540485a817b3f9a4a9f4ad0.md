### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through the shared `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. A pool admin who allowlists the router to enable router-based swaps for their curated users inadvertently opens the pool to every user, because any non-allowlisted user can route through the same router and pass the check.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact `sender` value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(recipient, ...)` directly. The pool sees `msg.sender = router`, so `sender = router` is what the extension checks. The actual user who initiated the call is invisible to the extension.

A pool admin who wants to allow router-based swaps for their allowlisted users must add the router to the allowlist:

```
allowedSwapper[pool][router] = true
```

Once the router is allowlisted, **any** user — including those explicitly not in the allowlist — can call `router.exactInputSingle(...)` and the extension will pass, because it only sees the router address.

This is structurally different from `DepositAllowlistExtension`, which correctly checks `owner` (the economic actor, the position owner) rather than `sender` (the intermediary):

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The deposit allowlist correctly enforces the restriction even when `MetricOmmPoolLiquidityAdder` is used as the intermediary, because `owner` (the position owner) is checked, not `sender` (the adder contract). The swap allowlist has no equivalent "actual swapper" field to check — the pool's `swap()` interface does not expose the originating user, only `msg.sender`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist invariant — "only approved addresses may swap" — is broken. Non-allowlisted users can execute swaps, drain liquidity at oracle prices, and interact with the pool as if they were approved. This is a direct loss of the access-control guarantee the pool admin configured, with fund-impacting consequences for LP positions in a curated pool.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery swap path. Any pool admin who wants their allowlisted users to be able to use the router (rather than calling the pool directly) must allowlist the router. This is a natural operational step. Once done, the bypass is available to every user with no special knowledge or privilege required — a single `exactInputSingle` call through the router suffices.

---

### Recommendation

The `beforeSwap` hook should check the actual economic actor. Two options:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData`, and the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `sender` and `recipient` together, or add an originator field to the swap interface**: The pool could expose an `originator` parameter (analogous to `owner` in `addLiquidity`) that the router fills with `msg.sender`, and the extension checks `originator` instead of `sender`.

The simplest immediate fix consistent with the existing design is to mirror the deposit allowlist pattern: add an `originator` address to `pool.swap()` that the router sets to `msg.sender`, and have `SwapAllowlistExtension` check `originator` rather than `sender`.

---

### Proof of Concept

**Setup:**
- Pool configured with `SwapAllowlistExtension` as `beforeSwap` hook.
- Admin allowlists Alice: `swapExtension.setAllowedToSwap(pool, alice, true)`.
- Admin allowlists the router so Alice can use it: `swapExtension.setAllowedToSwap(pool, router, true)`.
- Bob is not allowlisted.

**Attack:**
```solidity
// Bob calls the router directly — no allowlist entry for Bob
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// ↑ Succeeds. Extension sees sender=router (allowlisted), not bob.
```

**Why it passes:** `pool.swap()` is called by the router, so `msg.sender = router`. `_beforeSwap(router, ...)` is invoked. `SwapAllowlistExtension` checks `allowedSwapper[pool][router]` → `true` → no revert. Bob's swap executes.

**Contrast with direct call:**
```solidity
vm.prank(bob);
pool.swap(bob, true, 1000, 0, "", "");
// ↑ Reverts: sender=bob, allowedSwapper[pool][bob]=false → NotAllowedToSwap
```

The bypass is available to any user through the shared router whenever the router itself is allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
