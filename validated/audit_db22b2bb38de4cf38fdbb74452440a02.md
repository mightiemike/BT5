Audit Report

## Title
`SwapAllowlistExtension` grants allowlist bypass to any user when the router is allowlisted — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the raw `msg.sender` of `MetricOmmPool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. Any pool admin who allowlists the router — the only way to let allowlisted users use the standard periphery — simultaneously grants unrestricted swap access to every address on the network, completely defeating the per-user access control the extension is designed to enforce.

## Finding Description
The call chain is confirmed in production code:

1. `MetricOmmPool.swap()` captures `msg.sender` and passes it as `sender` to `_beforeSwap`: [1](#0-0) 

2. `ExtensionCalling._beforeSwap()` encodes that value verbatim into the extension call: [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap()` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly without forwarding any user identity — the router is always `msg.sender` from the pool's perspective: [4](#0-3) 

When the router is allowlisted (`allowedSwapper[pool][router] = true`), the check `allowedSwapper[pool][sender]` passes for every call arriving through the router regardless of who the actual end-user is. There is no mechanism in the pool or extension to recover the original caller's identity.

## Impact Explanation
The `SwapAllowlistExtension` is the sole on-chain enforcement layer for restricted pools (e.g., KYC-gated or counterparty-restricted pools). Bypassing it grants any unprivileged user full swap access to pool liquidity at oracle-derived prices, enabling unauthorized price-impact trades against LP capital. This constitutes broken core pool functionality and unauthorized access to pool funds, meeting the contest's High/Medium threshold for broken access control with direct fund impact.

## Likelihood Explanation
- The router is a public, permissionless contract — any EOA or contract can call it.
- Allowlisting the router is the natural and expected operational step for any pool admin who wants allowlisted users to use the standard swap UI.
- No special precondition beyond the router being allowlisted, which is the common-case configuration.
- The `allowedSwapper` mapping is public and on-chain verifiable, so an attacker can confirm the router is allowlisted before executing.

## Recommendation
The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Require the router to forward the real user identity** — add a `swapper` field to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension decodes and checks that field when `sender` is a recognised router address.
2. **Check `tx.origin` as a fallback** — if `sender` is a known contract/router, fall back to `tx.origin`. Acceptable here because the extension is already a trust-gating mechanism and `tx.origin` is the correct identity for EOA-initiated flows.

## Proof of Concept
```solidity
// Pool deployed with SwapAllowlistExtension.
// Admin allowlists router so alice (whitelisted) can use the UI.
allowlistExt.setAllowedToSwap(pool, address(router), true);
allowlistExt.setAllowedToSwap(pool, alice, true);
// bob is NOT allowlisted.

// bob calls the router — extension sees sender=router, passes.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        recipient: bob,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds; bob bypassed the allowlist.
```

`beforeSwap` receives `sender = address(router)`, `allowedSwapper[pool][router]` is `true`, the guard returns `IMetricOmmExtensions.beforeSwap.selector`, and the swap executes. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
