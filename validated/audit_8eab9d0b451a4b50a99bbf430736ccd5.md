Audit Report

## Title
SwapAllowlistExtension Bypassed via Router: `sender` Reflects Router Address, Not Original User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router contract address, not the originating user. A pool admin who allowlists the router to support router-mediated swaps for curated users inadvertently grants every user access, completely nullifying the allowlist policy.

## Finding Description

**Root cause:** `MetricOmmPool.swap` passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — when the router is the direct pool caller, `sender` is the router address: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original user's identity. The original user's address is stored in transient storage for the payment callback only, never passed to the pool's extension path: [4](#0-3) 

**Contrast with `DepositAllowlistExtension`:** the deposit guard correctly checks `owner` (the economic beneficiary explicitly passed by the pool), not `sender` (the direct caller). The swap interface carries no equivalent "original user" field: [5](#0-4) 

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension` as `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow allowlisted users to use the canonical router.
3. Admin does **not** allowlist attacker EOA.
4. Attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Pool receives `swap()` with `msg.sender = router`.
6. Extension checks `allowedSwapper[pool][router] == true` → passes.
7. Swap executes; attacker receives output tokens from a curated pool they are explicitly excluded from.

**Existing guards are insufficient:** The extension has no access to the original user's address. The `extensionData` field is passed through but the extension ignores it entirely (the `bytes calldata` parameter is unnamed and unused in `beforeSwap`). There is no on-chain mechanism within the current interface for the router to attest the originating user to the extension.

## Impact Explanation

The allowlist policy of a curated pool is completely nullified whenever the router is allowlisted. Unauthorized counterparties execute swaps at live oracle prices against LP positions, constituting a direct loss of LP value to unauthorized parties. This matches the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact categories. The smart audit pivot for allowlist bypass through router is directly triggered.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical swap entrypoint. Any pool admin who wants allowlisted users to use the router must allowlist the router address — there is no other supported path. Once the router is allowlisted (a natural, non-malicious configuration), any unprivileged EOA can call `exactInputSingle` or `exactInput` to bypass the gate. No special privileges are required; the bypass is repeatable by any address.

## Recommendation

1. **Pass originator through `extensionData`:** Have the router encode `msg.sender` into `extensionData` before calling `pool.swap()`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that value when present.
2. **Add an originator field to the swap extension interface:** Mirror the `owner` pattern from `DepositAllowlistExtension` — add a dedicated `originator` parameter to `IMetricOmmExtensions.beforeSwap` so the pool can pass a verified identity.
3. **Document incompatibility:** If the interface is not changed, explicitly document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and pools using it must require direct pool calls only.

## Proof of Concept

```
// Setup
pool = deploy pool with SwapAllowlistExtension as beforeSwap hook
admin.setAllowedToSwap(pool, router, true)   // allowlist router for curated access
// attacker EOA is NOT allowlisted

// Attack
attacker.call(
  MetricOmmSimpleRouter.exactInputSingle({
    pool: pool,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: X,
    amountOutMinimum: 0,
    recipient: attacker,
    extensionData: ""
  })
)

// Result
// pool.swap() called with msg.sender = router
// SwapAllowlistExtension.beforeSwap(sender=router, ...)
//   → allowedSwapper[pool][router] == true → PASSES
// Swap executes; attacker receives token1 output
// Allowlist policy completely bypassed
```

A Foundry integration test can confirm this by deploying the pool with the extension, calling `setAllowedToSwap(pool, router, true)`, then calling `exactInputSingle` from an un-allowlisted address and asserting the swap succeeds.

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
