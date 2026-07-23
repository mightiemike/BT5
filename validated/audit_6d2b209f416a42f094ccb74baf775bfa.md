Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to permit allowlisted users to trade via the standard periphery, every unprivileged user can bypass the allowlist by routing through the same public router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly — making the router the `msg.sender` inside `pool.swap`: [4](#0-3) 

The originating user's address is stored only in transient storage as the payer (`_setNextCallbackContext(..., msg.sender, ...)`), but is never forwarded to the extension as `sender`. The allowlist check therefore becomes `allowedSwapper[pool][router]` — a single binary flag for the entire router — rather than `allowedSwapper[pool][actual_user]`.

The same structural problem exists in the multi-hop `exactInput` path for all hops, where the router is also the direct caller of `pool.swap`: [5](#0-4) 

The transient payer slot (`T_PAYER_SLOT`) is readable only within the router's callback context and is not exposed to extensions in any standardized way: [6](#0-5) 

This creates an irresolvable dilemma: if the router is not allowlisted, allowlisted users cannot use the router at all; if the router is allowlisted, every unprivileged user bypasses the allowlist via the router.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses. Once the pool admin allowlists the router (the natural operational step to support normal periphery usage), the allowlist is effectively open to the public. Any address can call `exactInputSingle` or `exactInput` on the router and the extension will pass because it sees the allowlisted router address, not the caller. Non-allowlisted users can execute swaps on a pool that was supposed to be restricted, constituting broken core pool functionality and direct bypass of an admin-configured access control boundary.

## Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router — a configuration any operator would make to support normal periphery usage. The exploit itself requires no special privilege: any EOA calls a public router function with no access control on its entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` are all `external payable` with no caller restrictions). [7](#0-6) 

## Recommendation

The `SwapAllowlistExtension` must gate on the originating user, not the immediate pool caller. Two complementary approaches:

1. **Pass the originating user through `extensionData`.** The router already stores `msg.sender` in transient storage as the payer. The router could encode the originating user inside `extensionData` in a standardized envelope that the extension decodes and checks when `sender` is a known router.

2. **Expose transient payer to extensions.** The pool could expose a transient-storage read so extensions can retrieve the true originator, or the router could pass the originating user inside `extensionData`.

Note that `DepositAllowlistExtension` correctly checks `owner` (the position owner, not `sender`), so it is not affected by this specific path: [8](#0-7) 

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
  - bob is NOT allowlisted

Attack:
  - bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender to pool = router address
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - bob's swap executes on the restricted pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; bob trades on a curated pool without being allowlisted
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
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

**File:** metric-periphery/contracts/libraries/TransientCallbackPool.sol (L66-68)
```text
  function getPayer() internal view returns (address payer) {
    payer = _tloadAddress(T_PAYER_SLOT);
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
