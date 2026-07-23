Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is set to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, `sender` is the router's address, not the end user's. A pool admin who allowlists the router — the natural step to let their curated users trade through the supported periphery — inadvertently grants every unprivileged user the ability to bypass the per-user allowlist entirely.

## Finding Description

**Root cause — `MetricOmmPool.swap()` passes `msg.sender` verbatim as `sender`:**

`MetricOmmPool.swap()` passes `msg.sender` directly as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that same `sender` value to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user: [3](#0-2) 

**Router does not forward the real user:** `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with `""` as `extensionData` — the original `msg.sender` (the end user) is never encoded or forwarded: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Contrast with `DepositAllowlistExtension`:** The deposit allowlist correctly gates `owner` (the position beneficiary), not `sender` (the operator/payer), so a router-mediated deposit cannot bypass it: [6](#0-5) 

The swap allowlist applies the wrong operand — it checks the intermediary (router) instead of the economically relevant actor (the end user).

**Existing tests do not cover the router path:** The unit test `test_revertsWhenSwapperNotAllowed` only exercises the direct-pool path (`vm.prank(address(pool)); extension.beforeSwap(swapper, ...)`), never the router-mediated path where `sender` would be the router address: [7](#0-6) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties loses all access control the moment the router is allowlisted. Any unprivileged address can execute swaps on the restricted pool at oracle-determined prices, draining LP value without the pool admin's consent. This is a direct loss of LP principal and a complete curation failure on pools that rely on the allowlist as their primary safety boundary. The corrupted value is `allowedSwapper[pool][router]` being used as a proxy for per-user authorization when it is not.

## Likelihood Explanation

The scenario is not hypothetical. The router is the documented, supported periphery path for end users. A pool admin who allowlists individual users and then wants those users to be able to use the router has no mechanism to say "allow alice through the router" — the only available action is `setAllowedToSwap(pool, router, true)`. The moment the admin takes that natural step, the allowlist is fully bypassed for all users. No special privilege is required: any EOA can call `router.exactInputSingle()`.

## Recommendation

The extension must check the original end user's address, not the intermediary router's address. Two sound approaches:

1. **Router forwards the initiator:** `MetricOmmSimpleRouter` encodes `msg.sender` (the real user) into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router.

2. **Pool-level initiator field:** Add an `initiator` field to the `beforeSwap` hook signature that the pool always sets to the original transaction sender (via transient storage set at the top of `swap()`), and have the extension check `initiator` instead of `sender`.

The deposit allowlist's pattern — checking `owner` rather than `sender` — demonstrates the correct principle: gate the economically relevant actor, not the intermediary.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin allowlists alice:
       swapExt.setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router so alice can use it:
       swapExt.setAllowedToSwap(pool, router, true)
4. Charlie (never allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: charlie, extensionData: ""})
   → pool.swap(msg.sender=router, ..., extensionData="")
   → _beforeSwap(sender=router, ...)
   → allowedSwapper[pool][router] == true → PASSES
5. Charlie's swap executes on the curated pool.
   The allowlist provided zero protection against charlie.
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool stub, allowlist a router address, then call `extension.beforeSwap(routerAddress, ...)` pranked as the pool — assert it passes. Then call it with an unprivileged `charlie` address — assert it reverts. This demonstrates the asymmetry: the router path passes while the direct charlie path reverts, confirming the bypass.

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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-30)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
