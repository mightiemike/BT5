Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user, allowing any caller to bypass per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` receives `sender` as the `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. If the pool admin allowlists the router (a natural step to enable allowlisted users to use it), every unprivileged address can bypass the per-user allowlist by routing through the router, completely neutralizing the access control.

## Finding Description
`SwapAllowlistExtension.beforeSwap()` performs its identity check against the `sender` argument:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool), and `sender` is forwarded from `MetricOmmPool.swap()` as `msg.sender` of the pool call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← caller of pool.swap(), i.e. the router
```

`ExtensionCalling._beforeSwap()` passes this value unchanged to the extension via `abi.encodeCall`. When `MetricOmmSimpleRouter.exactInputSingle()` is used, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The real end-user address (`msg.sender` of the router call) is stored only in transient storage for the payment callback and is never forwarded to the extension. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][realUser]`.

A pool admin who wants allowlisted users to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check passes for every call arriving through the router regardless of who the actual caller is. Direct calls by non-allowlisted users correctly revert because `allowedSwapper[pool][bob]` is `false`, but the router path bypasses this entirely.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity()` correctly checks `owner` (the position owner, passed as a separate argument) rather than `sender` (the liquidity adder), avoiding this class of issue.

## Impact Explanation
Any unprivileged address can swap in a pool the admin intended to restrict to a specific set of addresses. This constitutes an admin-boundary break where an unprivileged path bypasses factory/pool role checks, and broken core pool functionality causing loss of funds: unauthorized users access oracle-driven pricing not intended for them, draining LP value at rates LPs did not consent to. The `SwapAllowlistExtension` becomes a no-op for any user willing to route through the public router.

## Likelihood Explanation
The bypass requires only that the pool admin allowlists the router — a natural and expected action for any pool that wants its allowlisted users to benefit from the router's slippage protection. No special privileges, flash loans, or oracle manipulation are required; a standard `exactInputSingle` call suffices. Any pool admin following the obvious setup path triggers this condition.

## Recommendation
Pass the actual end-user address through the hook chain. Two options:

1. **Router-side**: Store the real payer in transient storage and expose it via a standard interface (e.g., `IMetricOmmSwapInitiator`) that the extension can call back into the router to retrieve the originating address.
2. **Extension-side**: Require routers to forward the real user address in `extensionData`, with the extension decoding and verifying it. The pool admin would configure trusted router addresses separately.

The `DepositAllowlistExtension` pattern of checking `owner` (the position owner) rather than `sender` (the liquidity adder) is the correct model to follow.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps in a pool he was never authorized to access.

Direct pool call by Bob (`pool.swap(...)`) correctly reverts because `allowedSwapper[pool][bob]` is `false`. The bypass is exclusive to the router path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
