### Title
`SwapAllowlistExtension` gates on the router address instead of the end user, enabling complete allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the end user. If the pool admin allowlists the router address (the natural step to support router-mediated swaps on a curated pool), every user — including those the allowlist was designed to exclude — can bypass the guard by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` against the per-pool allowlist, where `msg.sender` (the pool) is used as the mapping key: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. In every router-mediated path the pool sees the router address as `sender`, not the originating user.

**Consequence:** A pool admin who wants to support router-mediated swaps on a curated pool must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` evaluates to `true` for every caller who routes through the router, regardless of whether that caller is individually permitted. The allowlist is completely bypassed.

---

### Impact Explanation

Curated pools deploy `SwapAllowlistExtension` to restrict trading to a defined set of addresses (e.g., KYC-verified counterparties, institutional participants). A complete bypass of this guard allows any unpermissioned address to execute swaps against the pool, violating the pool's access-control invariant and potentially exposing restricted liquidity to unauthorized traders. This constitutes a broken core pool functionality with direct fund-impact consequences for LPs who deposited under the assumption that only allowlisted counterparties could trade.

---

### Likelihood Explanation

The likelihood is high. Supporting the router is the standard user-facing path for the protocol. A pool admin who configures a curated pool and also wants users to access it through the official router will naturally call `setAllowedToSwap(pool, router, true)`. There is no documentation or on-chain signal warning that this single allowlist entry opens the gate to all users. The admin action is valid, semi-trusted, and follows the expected integration pattern.

---

### Recommendation

The `sender` forwarded to extension hooks must represent the **economic actor** (the end user), not the intermediary contract. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should pass the originating caller's address as a dedicated field in `callbackData` or `extensionData` so extensions can recover it. Alternatively, the router can expose a dedicated `swapOnBehalf(address user, ...)` entry point that the pool records.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should accept an optional `extensionData` payload that carries the verified end-user address when the direct `sender` is a known router, and fall back to `sender` for direct calls. This requires a trust model for which routers are permitted to assert a delegated identity.

The cleanest fix is for the pool to accept an explicit `onBehalfOf` parameter in `swap()` that extensions receive as the authoritative actor, with `msg.sender` still used for callback settlement.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists the router: setAllowedToSwap(pool, router, true)
  - Alice (address not in allowlist) wants to swap

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient, ...) — msg.sender at pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; Alice receives output tokens

Result:
  Alice, who is not individually allowlisted, successfully swaps on a
  curated pool. The allowlist guard is completely bypassed.
```

The existing integration test `test_allowedSwapSucceeds` in `FullMetricExtensionTest` allowlists `callers[0]` (a `TestCaller` contract) rather than the router, which is why this bypass is not caught by the current test suite. [5](#0-4)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
