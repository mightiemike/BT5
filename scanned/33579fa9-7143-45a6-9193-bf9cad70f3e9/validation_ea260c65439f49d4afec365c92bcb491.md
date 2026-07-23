### Title
`SwapAllowlistExtension` gates on the router address instead of the originating user, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the originating user. If the pool admin allowlists the router address to enable router-mediated swaps for their allowlisted users, every unprivileged user can bypass the individual allowlist by calling through the router.

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
```

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the value the pool passes — which is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` is used, the pool's `msg.sender` is the router:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

A pool admin who wants to allow router-mediated swaps for their allowlisted users will call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every call that arrives through the router — regardless of who the originating user is. Any non-allowlisted user can bypass the gate by calling `router.exactInputSingle()` or `router.exactInput()`.

The asymmetry with `DepositAllowlistExtension` is telling: the deposit extension correctly gates on `owner` (the position owner, not the caller), because the pool's operator pattern separates payer from owner. The swap extension has no equivalent "owner" concept and uses `sender` (the direct caller), which becomes the router when the periphery is used.

The `_validatePath` function in the router only validates array lengths, not pool-token connectivity, and the interface explicitly documents this as the caller's obligation. However, the allowlist bypass is not documented and is not the caller's obligation to prevent.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC'd counterparties, institutional participants, or whitelisted market makers) can have that restriction fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The bypassing user can execute swaps at oracle bid/ask prices, consuming LP liquidity that was intended only for authorized counterparties, moving the pool cursor, and potentially triggering downstream extensions (e.g., stop-loss, velocity guard) in ways the pool admin did not intend. This breaks the core access-control invariant the pool admin deployed the extension to enforce.

---

### Likelihood Explanation

The trigger is a pool admin calling `setAllowedToSwap(pool, router, true)` — a natural and expected action for any pool that wants to support router-mediated swaps for its allowlisted users. The admin has no on-chain signal that this opens the gate to all users. Once the router is allowlisted, any unprivileged user can exploit the bypass with a single `exactInputSingle` call. No special permissions, flash loans, or complex setup are required.

---

### Recommendation

The `sender` identity passed to `beforeSwap` is the direct caller of `pool.swap()`, which is the router when periphery is used. Two complementary fixes:

1. **Extension-side**: Decode the originating user from `extensionData` when present, falling back to `sender` for direct calls. The router should forward `msg.sender` (the original user) in `extensionData` for allowlist-gated pools.

2. **Router-side**: `MetricOmmSimpleRouter` should forward the originating user's address in `extensionData` so extensions can gate on the correct identity. The router already accepts per-hop `extensionDatas`; the convention should be documented and the extension should consume it.

Until fixed, pool admins must not allowlist the router address in `SwapAllowlistExtension`; instead they must require users to call `pool.swap()` directly.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, allowedUser, true)` — intending only `allowedUser` to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to let `allowedUser` use the router.
4. `bannedUser` (not allowlisted) calls `router.exactInputSingle({pool: pool, tokenIn: ..., zeroForOne: ..., ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bannedUser` successfully swaps in the restricted pool, bypassing the allowlist.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
