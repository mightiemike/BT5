Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, enabling full allowlist bypass — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument forwarded by the pool, which is the pool's own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the router, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to restore router usability for legitimate users simultaneously opens the pool to every user, defeating the curation guarantee entirely.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks it:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The extension therefore evaluates `allowedSwapper[pool][router]`, never seeing the originating user. The `DepositAllowlistExtension` avoids this by checking `owner` (the LP position owner, a separate argument from `sender`), but no equivalent economic-actor field exists in the swap interface.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses its curation guarantee the moment any non-allowlisted user routes through `MetricOmmSimpleRouter`. If the admin allowlists the router (required for legitimate users to use the primary swap interface), every user can bypass the allowlist with a single `exactInput` call. If the admin does not allowlist the router, allowlisted users are locked out of the primary swap interface. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users. This constitutes broken core pool functionality with direct exposure of LP funds to excluded actors.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. The bypass requires no privileged access, no special token, and no multi-step setup — a single `exactInput` or `exactInputSingle` call through the router suffices. Any non-allowlisted user who receives a `NotAllowedToSwap` revert on a direct pool call will naturally retry through the router. Pool admins who allowlist the router to restore legitimate user access will unknowingly open the gate to all users.

## Recommendation
The extension must check the address of the economic actor, not the intermediary. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This is acceptable given the router is a protocol-controlled contract.
2. **Extend the swap interface with an `originalSender` field**: Analogous to how `addLiquidity` separates `sender` (the caller) from `owner` (the economic actor), the swap interface could carry the originating user address separately, and `SwapAllowlistExtension` would check that field — mirroring the correct pattern already used in `DepositAllowlistExtension`.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is meant to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` so `userA` can use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. Router calls `pool.swap(recipient=userB, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. `userB`'s swap executes on the supposedly curated pool.

To block step 8, the admin must remove the router from the allowlist, which simultaneously breaks `userA`'s router access (step 3 reverts). No valid configuration exists. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
