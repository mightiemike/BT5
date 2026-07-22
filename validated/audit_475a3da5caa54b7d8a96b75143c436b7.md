### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender`, any swap routed through `MetricOmmSimpleRouter` presents the **router address** to the allowlist check, not the actual end-user. If the router is allowlisted — which is required for any allowlisted user to use it — the gate is open to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, so the pool sees `msg.sender = router`: [4](#0-3) 

The allowlist check therefore resolves to `allowedSwapper[pool][router]`. The pool admin faces an inescapable dilemma:

- **Do not allowlist the router** → every allowlisted user who calls through the router is blocked, breaking the normal UX path.
- **Allowlist the router** → the check passes for *any* caller who routes through the router, because the router is a public, permissionless contract. The allowlist is completely bypassed.

The same structural problem exists in the multi-hop `exactInput` path (intermediate hops use `address(this)` as payer) and `exactOutput` (recursive callback swaps use `msg.sender` as the pool-facing caller): [5](#0-4) 

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-gated, institutional, or whitelist-only pools) loses that restriction entirely once the router is allowlisted. Any unprivileged user can execute swaps against the pool, draining LP assets at oracle-quoted prices. Because the pool's oracle-based pricing does not self-protect against unauthorized counterparties, the LP's entire deposited principal is exposed to unrestricted trading.

---

### Likelihood Explanation

The router is the standard, documented entry point for end-users. A pool admin who wants allowlisted users to trade via the router must allowlist the router address. This is the expected operational configuration, making the bypass reachable in every realistic deployment of `SwapAllowlistExtension` that supports router-mediated swaps. No special privileges, flash loans, or unusual token behavior are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **economic actor**, not the immediate caller. Two sound approaches:

1. **Check `sender` against the allowlist only when `sender` is not a trusted router; otherwise check the payer stored in the router's transient context** — but this couples the extension to the router, which is fragile.

2. **Preferred**: Require the pool to pass the original end-user identity through a dedicated field (e.g., a separate `originator` argument in the hook interface), or have the router forward the real user address inside `extensionData` and have the extension decode and verify it. The extension should never trust `sender` when `sender` may be a public intermediary.

A minimal immediate fix: `SwapAllowlistExtension` should revert if `sender` is a known public router unless the real user identity is verifiably embedded in `extensionData`.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists only `alice` and the router (so alice can use the router).
allowlist.setAllowedToSwap(pool, alice, true);
allowlist.setAllowedToSwap(pool, address(router), true);

// Eve (not allowlisted) calls the router directly.
// pool.swap receives msg.sender = router → allowedSwapper[pool][router] = true → passes.
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: eve,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Eve successfully swaps on a pool she was never authorized to touch.
```

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
