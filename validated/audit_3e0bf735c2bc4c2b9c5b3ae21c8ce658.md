### Title
`SwapAllowlistExtension` gates the router address instead of the end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to **all** users, completely defeating the per-user allowlist.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` with itself as `msg.sender`: [4](#0-3) 

So the extension sees `sender = router`, not the end user. The pool admin faces an impossible choice:

- **Do not allowlist the router** → legitimate allowlisted users cannot use the standard periphery (DoS on the normal flow).
- **Allowlist the router** → every user on the network can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router and bypass the per-user restriction entirely.

Contrast this with `DepositAllowlistExtension`, which correctly checks `owner` (the economically relevant LP position owner), not `sender`: [5](#0-4) 

The asymmetry confirms the swap allowlist is binding to the wrong actor.

### Impact Explanation

Any user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter` once the pool admin allowlists the router. Unauthorized users gain full swap access to a pool that was intended to be restricted (e.g., KYC-gated, institutional-only, or risk-bounded). This breaks the core invariant: *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."* Unauthorized trading on a restricted pool can cause direct LP principal loss if the pool's risk parameters were calibrated for a specific, controlled set of counterparties.

### Likelihood Explanation

The trigger is a pool admin allowlisting the router — a natural and expected operational step for any pool that wants its allowlisted users to use the standard periphery. The router is the primary user-facing entry point documented and deployed by the protocol. A pool admin who reads the allowlist docs and wants to enable router-mediated swaps for their users will make exactly this configuration choice, unknowingly opening the pool to all users.

### Recommendation

The extension must gate the **end user**, not the immediate pool caller. Two viable approaches:

1. **Pass the original initiator through the router**: have the router encode `msg.sender` (the end user) into `extensionData` and have the extension decode and check it. This requires a convention between the router and the extension.
2. **Check `recipient` instead of `sender`**: for swap allowlists the recipient is often the economically relevant actor; however this is also router-controlled and has the same problem.
3. **Preferred**: mirror the deposit allowlist pattern — require the pool to pass the original EOA initiator as a dedicated field, or have the router forward the real user address in a standardized `extensionData` slot that the allowlist extension reads and verifies.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  // only alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true) // to let alice use the router
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) with msg.sender = router
6. Extension checks allowedSwapper[pool][router] == true  → passes
7. Bob's swap executes on the restricted pool — allowlist fully bypassed.
``` [6](#0-5) [7](#0-6)

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
