### Title
SwapAllowlistExtension gates the router address instead of the end-user identity, allowing any unprivileged user to bypass the per-user swap allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. A pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the gate to every user on the network.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to the extension:

```solidity
// ExtensionCalling.sol:160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. When the user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()`, so `sender = address(router)`.

The allowlist therefore checks whether the **router** is approved, not whether the **end user** is approved. A pool admin who wants allowlisted users to be able to use the standard router UX must add `address(router)` to `allowedSwapper[pool]`. The moment they do, every user on the network can bypass the allowlist by routing through the same public router.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (position owner), not `sender` (the adder contract), which correctly identifies the economic beneficiary regardless of the call path.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC-verified or otherwise approved addresses loses that restriction entirely for any user who calls `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` once the router is allowlisted. Unauthorized users can trade against the pool's liquidity at oracle-anchored prices, extracting value from LP positions that were provisioned under the assumption that only vetted counterparties would trade.

### Likelihood Explanation

The trigger condition — the router being allowlisted — is the natural and expected configuration for any pool that wants to support the standard periphery UX. The `MetricOmmSimpleRouter` is the primary public swap interface. A pool admin who deploys `SwapAllowlistExtension` and then adds the router to the allowlist (to let their approved users trade via the router) will unknowingly open the pool to all users. No special privilege or malicious setup is required from the attacker; calling `router.exactInputSingle` with a valid pool address is sufficient.

### Recommendation

The extension must receive and check the **originating user identity**, not the immediate `pool.swap()` caller. Two viable approaches:

1. **Pass the end-user through the extension payload**: require the router to encode `msg.sender` in `extensionData` and have the extension decode and verify it. This requires a coordinated change to the router and the extension interface.

2. **Check `sender` only for direct calls; require a separate allowlist entry for router-mediated calls**: add a `trustedRouter` concept so that when `sender == router`, the extension reads the actual payer from transient storage or a signed payload.

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is a known router address, forcing direct pool interaction for allowlisted pools.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is KYC-approved
  allowedSwapper[pool][address(router)] = true // admin adds router so alice can use it

Attack:
  bob (not KYC-approved) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, tokenOut: token1, ...})

  Execution path:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)          // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router)
            → allowedSwapper[pool][router] == true  ✓ PASSES
      → swap executes, bob receives token1

Result: bob bypasses the allowlist and trades on a restricted pool.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
