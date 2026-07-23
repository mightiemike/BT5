Audit Report

## Title
`SwapAllowlistExtension` gates on router address instead of actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to restore router usability, any non-allowlisted address can bypass the curation gate by calling the router. If the router is not allowlisted, all router-mediated swaps revert even for individually allowlisted users.

## Finding Description
In `MetricOmmPool.swap()`, the pool passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router, not the user
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` gates on that `sender`:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool's `msg.sender` is the router. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The same applies to `exactInput` (L104), `exactOutputSingle` (L136), `exactOutput` (L165), and the recursive callback hop at L220-228 where `pool.swap(msg.sender, ...)` is called with `msg.sender` being the router itself.

**Contrast with `DepositAllowlistExtension`**: the deposit allowlist correctly checks `owner` (the actual position owner passed explicitly by the caller), not `sender` (the liquidity adder contract). The `beforeAddLiquidity` hook receives both `sender` and `owner` as distinct parameters, enabling correct identity gating. The `beforeSwap` hook has no equivalent separation — it only receives `sender`, which collapses to the router address on any router-mediated path.

## Impact Explanation
**Bypass path (critical):** A pool admin who wants to allow router-based swaps must add the router to the allowlist. Once `allowedSwapper[pool][router] == true`, every call arriving through the router passes the check regardless of who the actual user is. Any non-allowlisted address can call `exactInputSingle` and trade on a pool intended to be restricted to specific counterparties (KYC'd users, institutional LPs, etc.). The allowlist provides zero protection against router-mediated access.

**Broken-flow path (medium):** If the pool admin does not allowlist the router, every router-mediated swap reverts with `NotAllowedToSwap`, even for users who are individually allowlisted. The router — the primary supported periphery path — becomes completely unusable for any allowlisted pool.

Both outcomes directly affect user funds: in the bypass case, unauthorized parties trade on curated pools; in the broken-flow case, legitimate users cannot enter or exit positions through the supported interface.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary production swap interface. Any pool that deploys `SwapAllowlistExtension` to restrict access will immediately encounter one of the two failure modes the first time a user attempts a router swap. The trigger requires no privileged access, no special token behavior, and no unusual timing — a standard `exactInputSingle` call is sufficient. The bypass requires only that the pool admin has allowlisted the router (the only rational choice to restore usability), after which any address can exploit it.

## Recommendation
The `beforeSwap` hook should gate on the original caller's identity, not the intermediary's. Two complementary fixes:

1. **In `SwapAllowlistExtension`**: require the router to forward the real user address in `extensionData` and decode it in the hook — analogous to how `DepositAllowlistExtension` uses `owner` rather than `sender`.

2. **In `MetricOmmSimpleRouter`**: pass `msg.sender` (the real user) through `extensionData` on every hop so allowlist extensions can decode and verify it independently of the `msg.sender` chain.

3. **In the pool/extension interface**: document that `sender` is the direct pool caller and extensions must not use it as the economic actor identity, or add an `originator` field alongside `sender`.

## Proof of Concept
**Setup:**
- Deploy a pool with `SwapAllowlistExtension` configured on the `beforeSwap` hook.
- Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted swapper.
- Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-based swaps.

**Attack (bypass):**
1. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
2. Router calls `pool.swap(bob, ...)` — pool's `msg.sender` = router.
3. Pool calls `_beforeSwap(router, bob, ...)`.
4. Extension evaluates `allowedSwapper[pool][router]` → `true`.
5. Bob's swap executes successfully despite not being allowlisted.

**Broken-flow variant (router not in allowlist):**
1. Alice (allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
2. Extension evaluates `allowedSwapper[pool][router]` → `false`.
3. Reverts with `NotAllowedToSwap` — Alice cannot use the router at all. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
