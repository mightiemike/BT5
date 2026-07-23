Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` parameter, which is `msg.sender` of the pool's `swap()` call — the direct caller (e.g., `MetricOmmSimpleRouter`), not the end user. When a pool admin allowlists the router to permit authorized users to trade through it, every user who routes through that contract bypasses the per-user restriction, because the allowlist check resolves to the single router address. This collapses a per-user guard into a per-router guard, allowing any unauthorized user to trade in a restricted pool by calling through the allowlisted router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` is called by an end user, it calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

The pool's `msg.sender` is the router, so `sender = router` in `beforeSwap`. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. If the admin allowlisted the router (a natural action to allow authorized users to trade through the standard router), every user who calls the router passes the guard.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the actual LP position `owner`, not the intermediary caller: [5](#0-4) 

The two sibling extensions apply fundamentally different identity models; the swap extension uses the wrong one.

## Impact Explanation

A pool admin who deploys a restricted pool (e.g., a private institutional pool with tight bid/ask spreads) and allowlists `MetricOmmSimpleRouter` to allow authorized users to trade through it inadvertently grants access to every address that calls the router. Unauthorized users can execute swaps in a pool intended to be private, and if the pool offers favorable pricing (tight spread), they can drain LP value at below-market rates. LP principal is exposed to unauthorized counterparties at prices the LPs did not intend to offer to the general public. This is a direct, fund-impacting consequence meeting the "direct loss of user principal or owed LP assets" impact criterion.

## Likelihood Explanation

The trigger path requires no privileged access:
1. Pool admin deploys a pool with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — a natural operational action.
3. Any unauthorized user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting that pool.
4. The pool passes `sender = router` to `beforeSwap`; `allowedSwapper[pool][router] == true`; the check passes.
5. Unauthorized swap executes.

This is a realistic operational scenario. The divergence between admin intent and actual outcome is silent with no on-chain warning.

## Recommendation

Mirror the `DepositAllowlistExtension` pattern: check the actual user, not the intermediary. The pool's `swap()` interface exposes `recipient` as the swap beneficiary. A more robust fix is to add a dedicated `swapper` field (separate from `recipient`) to the `beforeSwap` hook signature, analogous to `owner` in the deposit path, so the extension can check the true initiator. At minimum, document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps for per-user access control.

## Proof of Concept

```
Setup:
  - Pool configured with SwapAllowlistExtension in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow authorized users to trade via MetricOmmSimpleRouter).
  - Unauthorized user (not on allowlist) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: unauthorizedUser, ...})
        → pool.swap(recipient=unauthorizedUser, ...)  [msg.sender = router]
          → _beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ (passes)
          → swap executes for unauthorized user

Result:
  - allowedSwapper[pool][unauthorizedUser] == false (never set)
  - But the guard passed because sender == router, which IS allowlisted.
  - Unauthorized user receives swap output; pool LPs bear the counterparty risk
    they intended to restrict.

Foundry test plan:
  1. Deploy pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, address(router), true).
  3. Prank as unauthorizedUser, call router.exactInputSingle targeting the pool.
  4. Assert swap succeeds despite allowedSwapper[pool][unauthorizedUser] == false.
  5. Assert allowedSwapper[pool][unauthorizedUser] is still false after the swap.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
