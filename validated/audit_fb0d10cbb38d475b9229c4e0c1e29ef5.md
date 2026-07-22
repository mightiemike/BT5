### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist Guard - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps using the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. If the router is allowlisted (required for any router-mediated swap to work on a curated pool), every unprivileged user bypasses the allowlist by routing through it.

### Finding Description

`SwapAllowlistExtension.beforeSwap()` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

`msg.sender` here is the pool (correct). `sender` is whatever the pool passes as the first argument to `_beforeSwap`. The pool passes its own `msg.sender` — the entity that called `pool.swap()`.

When `MetricOmmSimpleRouter.exactInputSingle()` executes:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the router contract. The pool then calls `_beforeSwap(msg.sender /*= router*/, recipient, ...)`, and `ExtensionCalling._beforeSwap` encodes that router address as `sender` in the extension call:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)  // sender = router address
    )
);
```

So `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly, making the router the `sender` seen by the extension.

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties or whitelisted market makers). The admin must either:

1. **Allowlist the router** — so that router-mediated swaps work at all. Any non-allowlisted user then calls `router.exactInputSingle()` and the extension sees `sender = router`, which is allowlisted. The allowlist is completely bypassed for all users.

2. **Not allowlist the router** — router-mediated swaps are blocked for everyone, including legitimately allowlisted users. The curated pool becomes unusable through the standard periphery path.

In scenario 1, the allowlist guard fails open for all public users, defeating the entire purpose of the extension. Any user can trade on a pool that was intended to be restricted, which constitutes a broken core pool functionality and admin-boundary break.

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the protocol.
- Any pool deployer who configures `SwapAllowlistExtension` and also wants router-mediated swaps to work will inevitably allowlist the router, triggering the bypass.
- No special privileges or unusual conditions are required — a standard `exactInputSingle` call suffices.
- The `DepositAllowlistExtension` does not share this bug because it checks `owner` (explicitly provided by the caller), not `sender` (implicitly `msg.sender` of the pool).

### Recommendation

The pool should pass the original end-user address as `sender` to extensions, not its own `msg.sender`. One approach: the router encodes the real user in `callbackData` or `extensionData`, and the pool extracts and forwards it. Alternatively, the pool can accept an explicit `sender` parameter in `swap()` that the router populates with `msg.sender` before calling the pool, and the pool forwards that to extensions. The `DepositAllowlistExtension` pattern (checking `owner`, which is explicitly supplied) is the correct model.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary for any router swap to work.
3. Attacker (not in the allowlist) calls `router.exactInputSingle(pool, ...)`.
4. Pool calls `_beforeSwap(msg.sender=router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router] == true` → swap proceeds.
6. Attacker successfully trades on a pool they were never authorized to access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
