### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User — Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` always sets `sender = msg.sender` (the direct caller of the pool), router-mediated swaps present the router's address as `sender` instead of the end-user's address. This creates an irreconcilable wrong-actor binding: either allowlisted users cannot use the router at all, or the pool admin must allowlist the router contract itself — which lets any unprivileged user bypass the allowlist entirely.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`.
3. Inside `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, ...)` — here `msg.sender` is the **router**, not the end-user.
4. `ExtensionCalling._beforeSwap` forwards `sender = router_address` to every configured extension.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router_address]`.

The extension never sees the original user's address. The pool has no mechanism to propagate the end-user identity through the router.

**Relevant code:**

`MetricOmmPool.swap` (line 231):
```solidity
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` (line 37):
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (line 72–80):
```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
// msg.sender in pool = router address
```

**Two failure modes:**

| Admin configuration | Result |
|---|---|
| Router NOT allowlisted | Allowlisted users cannot swap through the router → broken core functionality |
| Router IS allowlisted | Any user can bypass the allowlist by routing through the router → full bypass |

Neither configuration achieves the intended policy.

### Impact Explanation

For curated pools (e.g., KYC-gated, institutional, or restricted-access pools) that deploy `SwapAllowlistExtension`, any unprivileged user can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The pool admin cannot simultaneously allow their allowlisted users to use the router and block non-allowlisted users. This is a direct policy bypass enabling unauthorized swaps against a pool that was explicitly configured to restrict access. Unauthorized swaps extract value from the pool's LP positions under conditions the pool admin did not intend to permit.

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any user who discovers the pool has a `SwapAllowlistExtension` can trivially route through `MetricOmmSimpleRouter` to bypass it. No special privileges, flash loans, or multi-transaction setup are required — a single `exactInputSingle` call suffices.

### Recommendation

The `sender` identity passed to extensions must reflect the economic actor, not the intermediary. Two approaches:

1. **Pass the original user through the router**: The router could pass the end-user address as an additional field in `extensionData`, and the `SwapAllowlistExtension` could decode it. However, this is opt-in and forgeable by any caller.

2. **Check `recipient` instead of `sender`** (if the pool's design intent is to gate who receives output): The `recipient` is the address that receives swap output and is set by the user, not the router. However, this changes the semantic of the allowlist.

3. **Preferred — gate at the router level**: The `SwapAllowlistExtension` should expose a view function `isAllowedToSwap(pool, user)` that the router checks before calling the pool, and the extension's `beforeSwap` should additionally check `recipient` (the address the user controls) rather than `sender`. Alternatively, the pool interface should be extended to carry an explicit `originator` field that the router populates with `msg.sender` and the extension checks.

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists only `trustedUser`.
// extension.setAllowedToSwap(pool, trustedUser, true);

// Attacker (not allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Pool receives msg.sender = router.
// _beforeSwap(router, attacker, ...) is called.
// SwapAllowlistExtension checks allowedSwapper[pool][router] → false (router not allowlisted).
// Reverts.

// Admin, wanting router to work for trustedUser, allowlists the router:
// extension.setAllowedToSwap(pool, router, true);

// Now attacker retries — passes, because allowedSwapper[pool][router] = true.
// Allowlist is fully bypassed for all users.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
