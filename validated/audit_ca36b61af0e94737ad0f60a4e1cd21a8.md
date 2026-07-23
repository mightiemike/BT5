Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool binds to `msg.sender` at the time of the `swap` call. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address, not the originating user. Any pool that allowlists the router to support router-mediated swaps for legitimate users simultaneously grants every non-allowlisted user the ability to bypass the per-user allowlist by routing through the router.

## Finding Description

**Call chain binding `sender` to the immediate caller:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension: [2](#0-1) 

**What the extension actually checks:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

**What the router passes as `msg.sender` to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, so the pool sees `msg.sender == address(router)`: [4](#0-3) 

The same applies to `exactInput` (L103-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181).

**The broken invariant:**

For any allowlisted user to swap via the router, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the extension sees `sender == router` for every router-mediated call and passes the check unconditionally, regardless of who the originating user is.

**Contrast with the correct pattern in `DepositAllowlistExtension`:**

`beforeAddLiquidity` ignores `sender` (first arg) and checks `owner` (second arg — the economic actor), which is invariant to who calls `addLiquidity`: [5](#0-4) 

The swap extension lacks an equivalent "originating user" argument to check.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (KYC, institutional, whitelist-only) provides zero protection once the router is allowlisted. Any unpermissioned user routes through `MetricOmmSimpleRouter` and trades freely. LP funds are exposed to trades from actors the pool was explicitly designed to exclude — a direct breach of the pool admin's access control policy and a potential regulatory/compliance failure for curated pools. Severity: **High** — broken core pool access control with direct fund-impact consequence.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool deploying `SwapAllowlistExtension` that also wants to support router-based swaps for its allowlisted users **must** allowlist the router, automatically triggering the bypass. The attacker requires no special privileges, no flash loan, and no unusual token behavior — a single `exactInputSingle` call suffices.

## Recommendation

`SwapAllowlistExtension` must gate on the originating user, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Preferred — encode the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention and that the extension verifies the caller is a trusted router.
2. **Mirror `DepositAllowlistExtension`'s pattern**: Have the pool pass the originating user explicitly as a dedicated argument (e.g., `owner`/`payer`) rather than reusing `sender` (the immediate caller), so the extension can check the economic actor regardless of routing path.

## Proof of Concept

```
Setup:
  - Pool P has SwapAllowlistExtension E configured on beforeSwap.
  - Admin calls E.setAllowedToSwap(P, router, true)   // required for router-mediated swaps
  - Admin calls E.setAllowedToSwap(P, alice, true)    // alice is the only intended user
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient=bob, ..., extensionData="")
     → pool sees msg.sender == address(router)
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[P][router] == true → passes
  5. Swap executes; bob receives output tokens.

Result: bob, a non-allowlisted user, successfully swaps on a curated pool.
Direct pool call by bob (P.swap(...)) correctly reverts because
allowedSwapper[P][bob] == false.
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
