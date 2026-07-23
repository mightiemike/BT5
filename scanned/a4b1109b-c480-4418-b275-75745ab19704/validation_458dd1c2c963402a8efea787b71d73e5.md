### Title
SwapAllowlistExtension Checks Router Address as Swapper, Allowing Any User to Bypass the Per-User Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of that call, so the extension sees the router address as the swapper — not the actual user. If the pool admin allowlists the router address to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist by routing through the shared public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops): [5](#0-4) [6](#0-5) 

Consequently, the extension always sees `sender = routerAddress` for every router-mediated swap. A pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, routerAddress, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][routerAddress]` returns `true` for every caller, and the per-user allowlist is completely inoperative for the router path.

The `DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` argument (the position owner), which the pool passes correctly regardless of whether the call comes through the `MetricOmmPoolLiquidityAdder` or directly. [7](#0-6) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) is fully open to any user the moment the router is allowlisted. Non-allowlisted users can execute arbitrary swaps, draining pool liquidity at oracle-derived prices, extracting value from LP positions, or violating the curation invariant the pool admin intended to enforce. This constitutes a broken core pool functionality and an admin-boundary break where an unprivileged path bypasses a configured access control.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed alongside the protocol. Any pool admin who wants allowlisted users to be able to use the router — the natural and expected UX — must allowlist the router address. There is no in-protocol mechanism to allowlist the router for specific users only; the granularity of `allowedSwapper` is per-pool, per-address. The bypass is therefore reachable on any curated pool whose admin has taken the obvious step of enabling router support.

---

### Recommendation

The extension must recover the original user identity rather than trusting the `sender` argument, which reflects only the immediate pool caller. Two sound approaches:

1. **Pass `tx.origin` as the checked identity inside the extension.** This is safe in this context because the extension is called synchronously from the pool during a non-reentrant swap, and `tx.origin` is the EOA that initiated the transaction. It correctly identifies the economic actor regardless of router intermediation.

2. **Require direct pool calls for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call `pool.swap` directly. This is a usage restriction, not a code fix, and is fragile.

Option 1 is the only approach that preserves both router usability and per-user curation:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    address actor = tx.origin; // recover the originating EOA
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][actor]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension configured
  admin = pool admin
  alice = allowlisted user
  eve   = non-allowlisted attacker
  router = MetricOmmSimpleRouter (public, shared)

Step 1 – admin allowlists alice and the router:
  admin calls: swapExt.setAllowedToSwap(pool, alice,  true)
  admin calls: swapExt.setAllowedToSwap(pool, router, true)
  // router is allowlisted so alice can use the router UI

Step 2 – eve calls the router directly:
  eve calls: router.exactInputSingle({pool: pool, tokenIn: token0, ...})
  // router calls pool.swap(...) with msg.sender = router
  // pool calls _beforeSwap(sender = router, ...)
  // extension checks allowedSwapper[pool][router] == true  ✓
  // swap executes — eve is NOT on the allowlist but bypasses it

Step 3 – verify:
  assertFalse(swapExt.isAllowedToSwap(pool, eve));   // eve is not allowlisted
  // yet the swap in Step 2 succeeded
```

The existing unit test `test_blocksSwapWhenSwapperNotAllowed` in `FullMetricExtensionTest` calls the pool directly (via `TestCaller`) and correctly blocks the swap. It does not exercise the router path, so the bypass is untested. [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-61)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
