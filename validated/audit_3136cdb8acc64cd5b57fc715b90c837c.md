Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the real user on router-mediated swaps, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swap access by checking the `sender` argument, which the pool populates with its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. This allows any unpermissioned user to bypass a curated allowlist by calling the router when the router is allowlisted, or permanently blocks allowlisted users from using the router when it is not.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly, making the pool's `msg.sender` the router contract: [4](#0-3) 

The same misbinding occurs in `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`. The extension therefore evaluates `allowedSwapper[pool][router]` — never `allowedSwapper[pool][real_user]`.

The `DepositAllowlistExtension` avoids this problem because `addLiquidity` takes an explicit `owner` parameter that is passed separately from `sender`, so the extension can gate on `owner` rather than the caller: [5](#0-4) 

No equivalent explicit-initiator parameter exists on the swap path.

## Impact Explanation

**Scenario A (router allowlisted):** A pool admin allowlists the router to support normal user flows. Any address — including non-allowlisted users — can call `router.exactInputSingle(...)` and the extension sees `allowedSwapper[pool][router] = true`, passing every swap regardless of the real initiator. This is a complete, permissionless allowlist bypass with direct fund impact: non-allowlisted users trade on a pool explicitly restricted to a curated set.

**Scenario B (router not allowlisted):** Allowlisted users who attempt to swap through the router are permanently blocked because the extension sees `allowedSwapper[pool][router] = false`. Core swap functionality is broken for the primary supported periphery path.

Both outcomes break the invariant that the allowlist gates the economically relevant actor.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint. Pool admins who want to support normal user flows will naturally allowlist the router, directly triggering Scenario A. No special role or setup is required beyond knowing the pool address — any user can call the router permissionlessly. The misbinding is structural and present on every router-mediated swap path.

## Recommendation

The pool must forward the original initiator's address rather than its own `msg.sender`. One approach: the router passes the real user address through the `extensionData` channel, and the extension decodes it. A more robust approach: the pool exposes a `swapWithSender(address realSender, ...)` entry point restricted to trusted periphery contracts, and the extension verifies the caller is a trusted router before accepting the forwarded identity. The `DepositAllowlistExtension` pattern (separate `owner` parameter) demonstrates the correct design.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Call `extension.setAllowedToSwap(pool, router, true)` — pool admin allowlists the router to support periphery users.
3. As an address with `allowedSwapper[pool][attacker] = false`, call `router.exactInputSingle(...)` targeting the pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`.
5. The extension receives `sender = router`, finds `allowedSwapper[pool][router] = true`, and allows the swap.
6. The non-allowlisted user successfully swaps on a curated pool, bypassing the intended access control.

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
