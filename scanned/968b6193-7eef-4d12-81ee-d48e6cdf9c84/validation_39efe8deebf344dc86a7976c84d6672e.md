### Title
SwapAllowlistExtension gates on the immediate pool caller (router address) instead of the end user, enabling full allowlist bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter against its per-pool allowlist. The pool passes `msg.sender` (the immediate caller of `pool.swap()`) as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-based swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the same router.

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
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

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist:

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

Here `msg.sender` is the pool (the extension's caller) and `sender` is the router when the user goes through `MetricOmmSimpleRouter`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original user's identity:

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

The pool sees `msg.sender = router`. The extension sees `sender = router`.

A pool admin who wants allowlisted users to be able to use the standard periphery must add the router to `allowedSwapper[pool][router]`. The moment they do, every user — allowlisted or not — can call `exactInputSingle` / `exactInput` / `exactOutput` through the router and the check passes because the router is allowlisted.

The only alternative is to never allowlist the router, which means allowlisted users are permanently locked out of the standard periphery and must call `pool.swap()` directly, breaking the intended UX and integration surface.

Note that `DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` parameter (the explicit position owner passed by the caller), not `sender`, so the liquidity adder path correctly enforces the depositor identity.

### Impact Explanation

Any user can swap on a curated pool that has `SwapAllowlistExtension` configured, provided the pool admin has allowlisted the router (a natural and expected admin action). The allowlist — the sole access-control mechanism for that pool — is completely bypassed. Disallowed users can drain liquidity from a pool that was intended to be restricted to a specific set of counterparties, causing direct loss of LP assets and breaking the pool's curation invariant.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical periphery entry point for swaps. Any pool admin who deploys a curated pool with `SwapAllowlistExtension` and also wants those users to be able to use the router (the standard path) will allowlist the router. This is the expected operational pattern. The bypass is therefore reachable on any curated pool that has not deliberately blocked the router, which is the common case.

### Recommendation

The extension must verify the actual end user, not the immediate pool caller. Two approaches:

1. **Pass the original user through the router**: Add a `payer` or `originator` field to the swap call or extension data, and have the router populate it. The extension then reads the originator from `extensionData` and verifies it against the allowlist. This requires a coordinated change to the router and extension interface.

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that `recipient` is always the end user (it is set to `params.recipient` in the router, which is the user-supplied address), the extension could gate on `recipient`. However, this is semantically different (recipient ≠ payer) and may not match the intended policy.

The cleanest fix is approach (1): the router should encode `msg.sender` into `extensionData` for allowlist-aware pools, and the extension should decode and verify it.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the standard periphery.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(sender=router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes on the curated pool despite never being allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
