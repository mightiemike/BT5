Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the router is that direct caller, so the extension checks whether the **router** is allowlisted rather than the original user. Any pool admin who allowlists the router to permit allowlisted users to trade through it simultaneously grants unrestricted swap access to every address.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool via `_callExtensionsInOrder`). `sender` is the first argument passed by `ExtensionCalling._beforeSwap`, which receives it from `MetricOmmPool.swap` at line 231:

```solidity
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` at lines 72–80 without encoding the original `msg.sender` into `extensionData`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // user-supplied, not auto-populated with original sender
    );
```

The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`. If the router is allowlisted (a prerequisite for any allowlisted user to use the router), every caller of `exactInputSingle` passes the check regardless of their own allowlist status. There is no mechanism in the router or pool to propagate the original user identity to the extension.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific participants (e.g., KYC'd users, institutional counterparties) is fully bypassed by any user routing through `MetricOmmSimpleRouter`. The non-allowlisted user executes real swaps against pool liquidity, receiving output tokens and paying input tokens through the router's callback. This constitutes broken core pool functionality: the configured access guard fails open on the standard public swap path, directly enabling unauthorized fund flows against pool liquidity.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the protocol's primary user-facing swap entry point. Any pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, address(router), true)`, which simultaneously opens the pool to all users. The admin has no protocol-level mechanism to distinguish router-mediated calls by original user. The bypass requires no special privileges — any EOA can call `exactInputSingle` with the router address.

## Recommendation

The extension must check the economically relevant actor — the original user — not the direct pool caller. Two approaches:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.
2. **Pool-side**: Add a transient-storage slot on the pool that the router populates with the original user before calling `swap()`, and have the extension read from it.

Either way, the extension's actor binding must be changed so that `allowedSwapper[pool][originalUser]` is checked, not `allowedSwapper[pool][router]`.

## Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists Alice (intended swapper)
ext.setAllowedToSwap(pool, alice, true);
// Pool admin also allowlists the router so Alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Attack: Bob (not allowlisted) routes through the router
// router.exactInputSingle() → pool.swap(msg.sender=router) → ext.beforeSwap(sender=router)
// ext checks allowedSwapper[pool][router] → true → swap succeeds
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    tokenIn: token1,
    extensionData: ""
}));
// Bob's swap succeeds despite not being on the allowlist
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
