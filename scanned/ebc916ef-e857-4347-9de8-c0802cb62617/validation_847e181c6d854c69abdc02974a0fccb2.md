### Title
SwapAllowlistExtension gates on the router address instead of the end user, allowing any unprivileged caller to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool passes `msg.sender` (the router) as `sender`. The extension therefore checks whether the **router** is allowlisted, not the actual end user. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the pool to every user on the network.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user enters through `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`, the router is `msg.sender` of the pool: [4](#0-3) 

For the multi-hop path, every hop uses the router as caller with an open price limit: [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. Two broken outcomes follow:

**Outcome A (allowlist bypass):** The pool admin allowlists the router address so that router-based swaps work. Every user on the network can now call `router.exactInputSingle()` and pass the check, because the router is allowlisted. The per-user curation is completely defeated.

**Outcome B (broken legitimate access):** The pool admin allowlists individual user addresses. Those users cannot trade through the router (router not allowlisted → revert), even though they are individually permitted. They must call `pool.swap()` directly, which requires them to implement the `IMetricOmmSwapCallback` interface themselves — an unreasonable burden that effectively locks them out of the primary periphery entry point.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the LP position owner), which is set by the caller and is not overwritten by the router: [6](#0-5) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-owned addresses) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker receives the same oracle-priced output as an allowlisted user. The pool's LP positions are exposed to unrestricted trading, which may violate regulatory requirements, drain concentrated liquidity at unfavorable oracle moments, or allow front-running by actors the pool admin explicitly excluded. This is a direct loss of the pool's curation guarantee and constitutes broken core pool functionality.

### Likelihood Explanation

The router is the primary user-facing entry point documented and deployed by the protocol. Any pool admin who wants users to trade through the router must allowlist it, which immediately triggers Outcome A. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices. Likelihood is high.

### Recommendation

The extension must check the **economic actor**, not the intermediary. Two sound approaches:

1. **Pass the original initiator through the router.** The router already knows `msg.sender` (the real user). It should forward that address as an authenticated field in `extensionData`, and the extension should decode and verify it. This requires a trust assumption that the router is the only permitted intermediary.

2. **Check `recipient` instead of `sender` for swap allowlisting.** The `recipient` is the address that receives output tokens and is the economically relevant actor. The extension already receives `recipient` as its second parameter (currently ignored). Gating on `recipient` is manipulation-resistant because the pool enforces token delivery to that address.

The cleanest production fix is approach (2): replace `sender` with `recipient` in the allowlist lookup, since `recipient` is the address that actually benefits from the swap and cannot be spoofed by an intermediary.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)   // allowlist the router so users can trade
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams{
          pool: pool,
          tokenIn: token0,
          zeroForOne: true,
          amountIn: X,
          amountOutMinimum: 0,
          recipient: attacker,
          ...
      })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...) [msg.sender = router]
      → _beforeSwap(sender=router, recipient=attacker, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes at oracle price
      → attacker receives token1 output

Result:
  attacker bypasses the allowlist and trades on a curated pool.
  Every non-allowlisted user can repeat this indefinitely.
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
