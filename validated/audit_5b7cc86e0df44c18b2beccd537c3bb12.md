Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` inside `MetricOmmPool.swap` — the immediate caller of the pool, i.e., the router contract. When a pool admin allowlists the router address (the natural action to let allowlisted users use the standard periphery), every unprivileged user can bypass the swap allowlist by routing through `MetricOmmSimpleRouter`, because the extension only ever sees `sender = router` and never inspects the end user's identity.

## Finding Description

**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension hook: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Router call path:** `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with `msg.sender = router` and passes `params.extensionData` directly from the caller — no end-user identity is encoded: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

**Existing guards are insufficient:** The only check in `beforeSwap` is the `allowedSwapper[pool][sender]` mapping. Once the router is in that mapping, the check passes for every caller regardless of their identity. There is no secondary check on `recipient` or any decoded field from `extensionData`.

**Contrast with `DepositAllowlistExtension`:** The deposit allowlist correctly gates on `owner` (the position owner, a stable identity), not `sender` (the caller/payer): [6](#0-5) 

The swap allowlist lacks an equivalent stable identity argument.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific addresses (KYC'd counterparties, whitelisted market makers, private participants) loses all enforcement the moment the router is allowlisted. Any unprivileged user can trade on the pool by routing through `MetricOmmSimpleRouter`. This is a complete allowlist bypass with direct fund-impact consequences: unauthorized users can drain liquidity, extract value at oracle-anchored prices, or interact with a pool contractually restricted to specific parties. This constitutes broken core pool access-control functionality causing potential loss of funds.

## Likelihood Explanation

Allowlisting the router is the natural and expected action for any pool admin who wants their allowlisted users to use the standard periphery interface rather than calling the pool directly. The router is a first-party, factory-validated contract. There is no documentation or on-chain warning that allowlisting the router opens the gate to all users. A pool admin following normal integration patterns will trigger this bypass without realizing it.

## Recommendation

The `beforeSwap` hook must gate on the **end user's identity**, not the immediate pool caller. The preferred fix mirrors the deposit allowlist pattern: the router should encode `msg.sender` (the end user) into `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and verify it. This requires a protocol-level convention for the `extensionData` layout when `SwapAllowlistExtension` is active. Alternatively, the router could be modified to always prepend the originating user address to `extensionData` for extension-aware pools, and the extension should decode and check that address instead of `sender`.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...)              // msg.sender = router
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes on the curated pool despite not being allowlisted

Expected: NotAllowedToSwap() revert for bob.
Actual:   Swap succeeds; bob trades on a pool restricted to allowlisted users only.
```

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
