### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the actual user. A pool admin who allowlists the router to enable standard router-mediated swaps inadvertently opens the allowlist to every user on the network, completely defeating the guard.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← immediate caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` with itself as `msg.sender`:

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

So `sender` seen by the extension is the router address, not the actual user. The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Legitimate users cannot use the standard router at all |
| **Allowlist the router** | Every user on the network can bypass the allowlist by routing through the router |

There is no configuration that achieves "only specific users may swap, and they may use the router."

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the position owner, which the liquidity adder always sets to `msg.sender` of the outer call), so the deposit guard is not affected.

---

### Impact Explanation

Any user can call `MetricOmmSimpleRouter.exactInputSingle` (a public, permissionless contract) targeting a pool whose allowlist includes the router address. The `beforeSwap` hook sees `sender = router`, passes the check, and the swap executes. The allowlist guard is completely nullified for router-mediated swaps. Pools that rely on the allowlist to restrict access — e.g., pools with concentrated liquidity intended only for specific counterparties, or pools with risk parameters calibrated for a known set of traders — will accept swaps from arbitrary addresses, exposing LP funds to unauthorized extraction.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the protocol's primary user-facing swap interface. Any pool admin who wants their allowlisted users to be able to use the standard router must allowlist the router address. This is the natural, expected operational step. The bypass is therefore reachable by any user against any pool that has taken this step, with no special privileges or setup required beyond calling the public router.

---

### Recommendation

The extension must gate the actual economic actor, not the immediate `pool.swap()` caller. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and the extension.

2. **Mirror the deposit pattern**: Introduce a `swapper` field analogous to `owner` in `addLiquidity` — a caller-supplied address that the pool passes to the extension and that the router always sets to `msg.sender`. The extension then checks that field instead of `sender`.

Until fixed, pools that require per-user swap gating should not rely on `SwapAllowlistExtension` when the router is in use.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice should be able to swap.
3. Admin also calls `setAllowedToSwap(pool, router, true)` so Alice can use the standard router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)`; `msg.sender` inside the pool is the router.
6. `beforeSwap` checks `allowedSwapper[pool][router]` → `true` → passes.
7. Bob's swap executes against the pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
