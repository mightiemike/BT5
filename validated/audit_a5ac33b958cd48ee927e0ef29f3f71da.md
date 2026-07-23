Audit Report

## Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` — Any User Can Swap Through an Allowlisted Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension::beforeSwap` gates swaps on `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool::swap`. When `MetricOmmSimpleRouter` intermediates, `sender` is the router's address, not the original user's address. If the pool admin allowlists the router (the only way to permit any router-mediated swap for legitimate users), the allowlist is completely bypassed for all users — any unprivileged address can swap in the restricted pool by routing through the router.

## Finding Description

`SwapAllowlistExtension::beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument, which originates from `MetricOmmPool::swap` passing its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // pool's msg.sender = direct caller of pool.swap()
    ...
```

`ExtensionCalling::_beforeSwap` forwards this value unchanged to the extension via `abi.encodeCall`. When `MetricOmmSimpleRouter::exactInputSingle` calls the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

The pool sees `msg.sender = router`. Therefore the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`. The original EOA is captured in `_setNextCallbackContext` for payment purposes only and is never forwarded to the extension. The pool admin faces an impossible choice: allowlist the router (every user bypasses the guard) or do not (every legitimate user is blocked from using the router).

## Impact Explanation

If the pool admin allowlists the router to enable router-mediated swaps for legitimate users — the expected operational path — any unprivileged attacker can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router targeting the restricted pool and the `beforeSwap` guard will pass. The allowlist, the pool's primary access-control mechanism, is fully neutralized. This constitutes unauthorized access to a restricted pool and broken core functionality of the extension guard.

## Likelihood Explanation

The router is a standard, publicly deployed periphery contract. Any pool using `SwapAllowlistExtension` that also wants to support router-mediated swaps must allowlist the router, making the bypass trivially reachable by any user with no preconditions, no special tokens, and no privileged access. The bypass is complete in a single transaction.

## Recommendation

The `sender` passed to the extension must represent the original user, not the intermediary. The most robust fix is to require the router to forward the original caller via `extensionData`: the router encodes `msg.sender` into `extensionData` at entry, and `SwapAllowlistExtension::beforeSwap` decodes and verifies it when `msg.sender` (the pool) is a known trusted pool. Alternatively, document and enforce that pools using `SwapAllowlistExtension` must not allowlist any router and users must call the pool directly.

## Proof of Concept

**Setup:**
- Pool P uses `SwapAllowlistExtension`.
- Pool admin calls `setAllowedToSwap(P, router, true)` to allow router-mediated swaps for legitimate users.
- Attacker address `A` is NOT in `allowedSwapper[P]`.

**Attack (single transaction):**
```solidity
router.exactInputSingle(ExactInputSingleParams({
    pool: P,
    recipient: attacker,
    tokenIn: token0,
    amountIn: X,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
```

**Execution trace:**
1. Router calls `P.swap(attacker, true, X, 0, "", "")` — pool sees `msg.sender = router`.
2. Pool calls `_beforeSwap(router, ...)`.
3. Extension checks `allowedSwapper[P][router]` → **true** → guard passes.
4. Attacker receives output tokens from a pool they were never authorized to access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
