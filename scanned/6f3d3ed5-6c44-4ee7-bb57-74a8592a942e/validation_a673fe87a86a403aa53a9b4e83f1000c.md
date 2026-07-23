### Title
`SwapAllowlistExtension` checks the router's address instead of the end-user's address, allowing any user to bypass the per-user swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps (a normal operational step), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding its own `msg.sender` as the `sender` argument to every configured extension. [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim and dispatches it to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called the pool: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point), the router calls `pool.swap(...)` directly, making the pool's `msg.sender` the router contract, not the end user: [4](#0-3) 

The same holds for multi-hop `exactInput` and `exactOutput` paths — every hop is initiated by the router, so every pool sees the router as `sender`: [5](#0-4) 

Contrast this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks the `owner` argument (the position owner, not the caller), so the deposit allowlist is not affected by the same issue: [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified users). To support router-mediated swaps — the primary user-facing entry point — the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the extension's check `allowedSwapper[pool][router]` passes for every swap that arrives through the router, regardless of who the end user is. Any non-allowlisted address can then trade in the restricted pool simply by calling `exactInputSingle` or `exactInput` on the router. The per-user allowlist is completely bypassed for all router-mediated swaps, breaking the pool's intended access-control invariant.

---

### Likelihood Explanation

The router is the canonical public entry point for swaps. A pool admin who deploys a curated pool and wants users to interact through the standard router will naturally allowlist the router. The admin has no indication from the contract or documentation that doing so defeats the per-user allowlist. The trigger is therefore a routine, expected administrative action rather than an exotic misconfiguration.

---

### Recommendation

The extension should gate the economically relevant actor — the end user — not the immediate caller of the pool. One approach is to pass the original `msg.sender` of the router call through `extensionData` and verify it inside the extension. A simpler alternative is to check `sender` only when `sender` is not a known router, and require a signed or transient-storage-attested user identity when the immediate caller is a router. At minimum, the `SwapAllowlistExtension` NatSpec and pool-admin documentation must warn that allowlisting the router grants unrestricted swap access to all users.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. A non-allowlisted user (`attacker`) calls `router.exactInputSingle(...)` targeting the pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`.
5. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
6. The swap executes successfully despite `attacker` never being individually allowlisted.
7. Direct call: `pool.swap(...)` from `attacker` → extension evaluates `allowedSwapper[pool][attacker] == false` → reverts `NotAllowedToSwap`.

The router path succeeds while the direct path correctly reverts, demonstrating the bypass. [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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
