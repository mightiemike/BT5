### Title
SwapAllowlistExtension gates the router address instead of the originating user, enabling full allowlist bypass through `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the extension checks whether the **router** is allowlisted, not the originating user. A pool admin who allowlists the router address (the natural action to enable router-mediated swaps) inadvertently opens the curated pool to every user on the network.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` argument forwarded by the pool:

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

`msg.sender` here is the pool; `sender` is whatever address called `pool.swap()`. In `MetricOmmSimpleRouter.exactInputSingle` the router calls the pool directly:

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

The pool receives `msg.sender = router`, so it passes `sender = router` to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same applies to every hop in `exactInput` and `exactOutput`.

The `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the position owner explicitly passed to `addLiquidity`), which `MetricOmmPoolLiquidityAdder` correctly sets to the economic beneficiary. No equivalent "owner" field exists on the swap path, so the swap extension is structurally unable to recover the originating user when the router is the intermediary.

---

### Impact Explanation

A pool admin who wants to allow allowlisted users to trade through the official router must add the router address to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who the originating user is. Any address on the network can then call `router.exactInputSingle(...)` targeting the curated pool and the extension passes unconditionally. The entire user-level curation is bypassed, allowing unauthorized parties to trade on a pool that was explicitly configured to restrict access. This constitutes a direct curation failure and, depending on pool design, can result in unauthorized extraction of LP value or fee revenue from a pool that was intended to be private.

---

### Likelihood Explanation

The likelihood is high. The router is the primary user-facing entry point documented and deployed alongside the protocol. A pool admin who configures a curated pool and wants their allowlisted users to be able to use the standard router will naturally add the router to the allowlist. There is no warning in the extension, the interface, or the NatDoc that doing so collapses the per-user gate to a per-contract gate. The bypass requires no special privilege: any EOA can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The extension must recover the originating user rather than the immediate pool caller. Two approaches:

1. **Extension-data forwarding**: The router encodes the originating `msg.sender` into `extensionData` for each hop, and the extension reads and verifies that field. This requires the extension to trust the router, so the router address itself must be verified (e.g., against a factory-registered router registry).

2. **Transient-storage sender**: The router writes the originating user into a transient storage slot before calling the pool, and the extension reads that slot. The extension must still verify that the writer is a trusted router.

Until one of these is implemented, the NatDoc of `SwapAllowlistExtension` should explicitly state that allowlisting the router address disables per-user gating, and pool admins who require user-level curation must require direct pool calls.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. A non-allowlisted user (`attacker`) calls `router.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. The pool calls `extension.beforeSwap(router, recipient, ...)`.
6. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. The attacker's swap executes on the curated pool despite never being individually allowlisted.

Contrast with a direct call: if `attacker` calls `pool.swap(...)` directly, the extension evaluates `allowedSwapper[pool][attacker] == false` → reverts. The bypass is exclusively available through the router path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
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
