Audit Report

## Title
SwapAllowlistExtension gates on router address instead of end-user, enabling unconditional allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's allowlist status rather than the actual end-user. If the router is allowlisted (the only way to make router-based swaps work on a curated pool), every user — including non-allowlisted ones — bypasses the guard entirely, defeating the pool admin's per-user curation policy.

## Finding Description

**Exact call path:**

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards `sender` verbatim into the encoded extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` keys its allowlist check on `sender` (the pool's `msg.sender`): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the pool's `msg.sender` the router: [4](#0-3) 

The same pattern applies in `exactOutputSingle`: [5](#0-4) 

And in `exactInput` for each hop: [6](#0-5) 

The `extensionData` bytes field is passed through the router to the pool and then to the extension, but `SwapAllowlistExtension.beforeSwap` never reads it (the `bytes calldata` parameter is unnamed and discarded): [3](#0-2) 

**Two broken scenarios:**

1. **Allowlist bypass (high impact):** Pool admin allowlists the router so router-based swaps work. Because `allowedSwapper[pool][router] = true`, every user — including non-allowlisted ones — passes the check by routing through `MetricOmmSimpleRouter`. Per-user curation is completely defeated.

2. **Allowlisted users locked out of router (medium impact):** Pool admin does not allowlist the router. Allowlisted users who call the router get `NotAllowedToSwap` because `allowedSwapper[pool][router] = false`, even though their own address is allowlisted. The router is unusable on any curated pool.

`DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly passed as a separate argument), not on `sender`: [7](#0-6) 

## Impact Explanation
A non-allowlisted user can trade on a curated pool that is supposed to restrict access to specific counterparties (e.g., KYC'd addresses, whitelisted market makers). The bypass is unconditional once the router is allowlisted, requires no special privileges, and is reachable through the standard public periphery path. This constitutes a direct break of the pool admin's curation boundary — an admin-boundary break — and, depending on the pool's purpose, can expose LPs to trades with unintended counterparties or allow extraction of value from pools designed for closed participant sets. [8](#0-7) 

## Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and also wants to support `MetricOmmSimpleRouter` faces this issue. The router is the primary public swap entrypoint in the periphery. A pool admin who allowlists the router (the only way to make the router work) immediately opens the pool to all users. The trigger requires no special timing, no privileged role, and no unusual token behavior — a single `exactInputSingle` call from any EOA suffices. [9](#0-8) 

## Recommendation
The extension must verify the actual end-user, not the intermediary. Two viable approaches:

1. **Pass user identity through `extensionData`:** Have the router encode `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and verify it. The extension must also verify that the encoding came from a trusted router (e.g., by checking `sender` is a factory-registered router) to prevent spoofing via user-controlled `extensionData`.

2. **Distinguish direct calls from router calls:** The extension can check whether `sender` is a known factory-registered router; if so, decode the actual user from `extensionData`; otherwise check `sender` directly.

The `DepositAllowlistExtension` pattern of using a separately passed, semantically meaningful identity argument (`owner`) rather than `sender` is the correct model. [7](#0-6) 

## Proof of Concept

```solidity
// Setup:
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists the router so router-based swaps work:
//   allowedSwapper[pool][router] = true
//   allowedSwapper[pool][alice]  = true   (intended allowlisted user)
//   allowedSwapper[pool][bob]    = false  (non-allowlisted user)

// Bob (non-allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: curated_pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));

// Call path:
//   MetricOmmSimpleRouter.exactInputSingle()
//     → pool.swap(recipient=bob, ..., extensionData="")   // msg.sender = router
//       → _beforeSwap(sender=router, ...)
//         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
//           → allowedSwapper[pool][router] == true  → passes
//
// Bob's swap executes successfully despite not being allowlisted.
// The check on allowedSwapper[pool][bob] is never evaluated.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][router] = true`, set `allowedSwapper[pool][bob] = false`, call `router.exactInputSingle` from `bob`, assert the swap succeeds (no `NotAllowedToSwap` revert). [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
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
