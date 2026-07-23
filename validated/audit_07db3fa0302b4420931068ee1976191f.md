### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool always sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the router address is allowlisted for a pool, every user — including those not individually allowlisted — can bypass the swap allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every `_beforeSwap` hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value the pool forwarded — the router's address, not the end user's: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Consequence:** A pool admin who wants to allow router-mediated swaps for their allowlisted pool has only two options:

1. **Do not allowlist the router** → allowlisted users cannot use the router at all (broken functionality).
2. **Allowlist the router** → every user, including those not individually allowlisted, can bypass the allowlist by routing through the router (security bypass).

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users. The allowlist is structurally broken for the router path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or protocol-controlled addresses) can be fully bypassed by any user who routes through `MetricOmmSimpleRouter`. Once the router is allowlisted — a natural and necessary step for any pool that wants to support the standard periphery — the allowlist provides zero protection. Unauthorized users can drain liquidity at oracle-derived prices, execute arbitrage, or interact with the pool in ways the pool admin explicitly intended to prevent.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router, which is the expected operational step for any allowlisted pool that also wants to support the standard periphery swap path. The `SwapAllowlistExtension` provides no warning or mechanism to distinguish end users behind the router. Any pool that enables both the allowlist extension and the router is affected. The attacker needs no special privileges — only the ability to call `MetricOmmSimpleRouter`.

---

### Recommendation

The `sender` identity passed to `beforeSwap` must represent the economic actor, not the intermediary. Two approaches:

1. **Router-level**: Have `MetricOmmSimpleRouter` accept and forward an explicit `realSender` parameter to `pool.swap`, and have the pool pass it through to extensions alongside `msg.sender`. This requires a pool interface change.

2. **Extension-level**: `SwapAllowlistExtension` should also check `allowedSwapper[pool][msg.sender_of_router]` — but this is impossible without the router forwarding the original caller.

The cleanest fix is to add an optional `originSender` field to the `swap` call that the pool passes to extensions as a separate argument, allowing extensions to gate on the true economic actor rather than the immediate caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is individually allowlisted)
  - allowedSwapper[pool][router] = true  (router allowlisted to enable periphery)
  - bob is NOT in allowedSwapper

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) with msg.sender = router
  3. Pool calls extension.beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true → passes
  5. Bob's swap executes successfully despite not being allowlisted

Result: bob bypasses the swap allowlist entirely by routing through the router.
```

The existing test `test_allowedSwapSucceeds` in `FullMetricExtensionTest` only exercises direct pool calls via `TestCaller`, never through `MetricOmmSimpleRouter`, so this bypass is untested. [5](#0-4)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
