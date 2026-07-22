### Title
`SwapAllowlistExtension` Gates the Router Address, Not the End User — Any User Can Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` at the pool call boundary. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the router is allowlisted for a pool, every user — including those explicitly not allowlisted — can bypass the per-user swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the argument just described: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At the pool call boundary, `msg.sender` is the router contract address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual end user's identity is never visible to the extension.

A pool admin who wants to allow allowlisted users to trade through the router must allowlist the router address itself. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every swap that arrives through the router, regardless of who the real user is. The per-user allowlist is silently voided for all router-mediated swaps.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the position owner explicitly supplied by the caller), not `sender`: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin intends to restrict trading to a specific set of addresses. Once the router is allowlisted (a natural step so that approved users can use the standard periphery), the allowlist is effectively open to the entire public for router-mediated swaps. Any user can trade on the restricted pool, draining LP value or executing trades the pool admin explicitly prohibited. This is a direct, fund-impacting bypass of a configured security control.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing swap interface. A pool admin who deploys a curated pool and wants approved users to have a normal trading experience will allowlist the router. The admin has no on-chain signal that doing so opens the pool to everyone — the allowlist mapping stores only a boolean per address, with no distinction between "this is a router" and "this is a user." The bypass is therefore reachable through ordinary, expected admin configuration.

---

### Recommendation

The extension must gate the actual end user, not the intermediary. Two complementary fixes:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. The extension decodes and checks that address instead of (or in addition to) `sender`.

2. **Separate router-level access control**: The router exposes its own allowlist that it enforces before calling the pool, so the pool-level extension only needs to gate direct callers. This requires the router to be a trusted, non-upgradeable contract.

Either approach must be applied consistently across all router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and the multi-hop callback path in `_exactOutputIterateCallback`. [6](#0-5) 

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to let approved users trade via the router.
3. Pool admin calls setAllowedToSwap(pool, alice, true)
   — alice is the only approved end user.
4. bob is NOT allowlisted.

Attack
──────
5. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
6. Router calls pool.swap(recipient, ...) — msg.sender at pool = router.
7. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...).
8. Extension evaluates: allowedSwapper[pool][router] == true → passes.
9. bob's swap executes on the curated pool.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
