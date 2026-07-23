### Title
`SwapAllowlistExtension::beforeSwap` gates the router address instead of the originating user, allowing any unprivileged caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict swaps on a curated pool to a specific set of allowed addresses. However, the `sender` argument it receives and checks is the immediate caller of `MetricOmmPool::swap` — which is `MetricOmmSimpleRouter` when users route through the periphery — not the originating EOA. A pool admin who allowlists the router to enable router-mediated swaps for their curated pool inadvertently opens the pool to every user, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool::swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap`: [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter::exactInputSingle` (or any other router entry point), the router is the entity that calls `pool.swap`: [4](#0-3) 

So the allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. The extension never sees the originating EOA.

**Consequence:** A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. The moment they do, the check `allowedSwapper[pool][router]` passes for every caller — any non-allowlisted user can bypass the restriction by routing through the public router.

The `DepositAllowlistExtension` does not share this flaw because it gates by `owner` (the position owner explicitly passed to `addLiquidity`), not by `sender`: [5](#0-4) 

---

### Impact Explanation

Any user who is not on the swap allowlist can execute swaps on a curated pool by calling `MetricOmmSimpleRouter::exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) once the router is allowlisted. The pool's entire curation policy — intended to restrict trading to vetted counterparties — is silently nullified. Trades that should have been blocked execute at live oracle prices, draining LP value to unauthorized parties.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router. This is a natural and expected action: a pool admin who deploys a curated pool and also wants their allowlisted users to benefit from the router's slippage protection and multi-hop routing will allowlist the router. The admin has no on-chain signal that doing so opens the pool to everyone. The router is a public, factory-verified contract, so allowlisting it appears safe. The mistake is easy to make and the bypass is immediately available to any user once it occurs.

---

### Recommendation

Pass the originating user through the swap path so the allowlist can gate the correct actor. Two concrete options:

1. **Router forwards the original caller**: Store the original `msg.sender` in transient storage at router entry (alongside the existing callback context) and expose it via a standard interface. The pool reads it and passes it as `sender` to extensions instead of its own `msg.sender`. This requires a coordinated change in `MetricOmmPool`, `ExtensionCalling`, and the router.

2. **Extension reads the router's stored payer**: `SwapAllowlistExtension::beforeSwap` calls back into the router (if `sender` is a known router) to retrieve the original payer from transient storage, then checks that address against the allowlist. This is more fragile but requires no core changes.

Either way, the invariant to enforce is: **the address checked by the allowlist must be the economic actor who initiated the transaction, not the intermediary contract that called `pool.swap`**.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the intended gated user
  allowedSwapper[pool][router] = true  // admin allowlists router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

  Execution trace:
    router.exactInputSingle()          // msg.sender = bob
      pool.swap(...)                   // msg.sender = router
        _beforeSwap(sender=router, ...)
          SwapAllowlistExtension.beforeSwap(sender=router, ...)
            check: allowedSwapper[pool][router] == true  ✓ passes
        swap executes, bob receives tokens

Result: bob bypasses the allowlist and swaps on a curated pool.
``` [6](#0-5) [1](#0-0) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
