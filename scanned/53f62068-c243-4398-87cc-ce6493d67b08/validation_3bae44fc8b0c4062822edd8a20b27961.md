### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool binds to its own `msg.sender` (the immediate caller). When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router — a natural action to let legitimate users use the router interface — every unpermissioned user can bypass the allowlist by routing through it.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

`MetricOmmPool.swap` binds `sender` to its own `msg.sender` before dispatching to the extension:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the pool's `msg.sender`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who wants legitimate allowlisted users to access the pool through the router must allowlist the router address. Once the router is allowlisted, the check passes for **every** caller of the router, regardless of whether that caller is individually permitted.

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional-only, or compliance-gated) that deploys `SwapAllowlistExtension` and allowlists the router loses all per-user access control for router-mediated swaps. Any unpermissioned address can trade on the restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). This constitutes a complete bypass of the pool's core access-control invariant, allowing unauthorized parties to extract liquidity, front-run allowlisted traders, or violate the pool's intended participant set.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a realistic and foreseeable action: pool admins who want their allowlisted users to benefit from the router's slippage protection, multi-hop routing, and deadline enforcement must allowlist the router, because without it every router-mediated swap reverts with `NotAllowedToSwap`. The protocol documentation and the `generate_scanned_questions.py` audit scaffold explicitly flag this exact path ("the hook must gate the same actor the pool designers thought they were allowlisting"). [5](#0-4) 

---

### Recommendation

Pass the actual end-user identity through the call chain. Two options:

1. **Preferred — thread the original `msg.sender` through the router**: have the router encode the real user address in `extensionData` and have the extension decode and check it. This requires a coordinated extension design.

2. **Simpler — check `sender` (the pool's `msg.sender`) only for direct pool calls, and require the router to be a trusted intermediary that enforces its own allowlist before calling the pool**: the router would check `isAllowedToSwap(pool, msg.sender)` before forwarding, and the pool-level extension would only gate direct callers.

The cleanest fix is to redesign `SwapAllowlistExtension.beforeSwap` so that when the immediate caller is a known router, it reads the actual user from a trusted source (e.g., a transient-storage slot set by the router before the pool call), rather than accepting the router address as the gated identity.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(bob, ...)`. The pool's `msg.sender` is the router.
6. `_beforeSwap(router, bob, ...)` is dispatched; the extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite not being individually allowlisted.

Direct call by Bob (`pool.swap(...)`) correctly reverts with `NotAllowedToSwap` because `allowedSwapper[pool][bob]` is `false`, confirming the bypass is exclusive to the router path. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
