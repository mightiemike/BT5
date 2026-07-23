Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on router address instead of actual user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router to enable router-based swaps for permitted users inadvertently grants every user — including those not on the allowlist — the ability to bypass the per-user gate by routing through the router.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-L240
_beforeSwap(
    msg.sender,   // pool's msg.sender — the router when called via router
    recipient,
    ...
);
``` [2](#0-1) 

`_beforeSwap` in `ExtensionCalling.sol` encodes this `sender` and passes it to the extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`. The actual user's address (`msg.sender` of the router call) is stored only in transient storage for the payment callback via `_setNextCallbackContext` — it is never forwarded to the pool or extension as the swapper identity:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-L80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. `DepositAllowlistExtension` does not share this flaw because it ignores `sender` and checks `owner`, the economically relevant party: [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC-gated LPs, regulated venues, curated market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool's LPs are exposed to swaps from arbitrary counterparties they did not intend to allow, breaking the core access-control invariant the extension is designed to enforce. In adversarial-selection scenarios (e.g., a pool restricted to low-adverse-selection flow), unrestricted access causes LP value leakage through unfavorable trades — a direct loss of LP assets above Sherlock thresholds.

## Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a natural and expected configuration: a pool admin who wants allowlisted users to use the standard router must allowlist the router. The admin has no mechanism to simultaneously allow router-based swaps for permitted users and block non-permitted users, because the extension provides no way to inspect the actual user behind the router. Any pool that enables router-based swaps for its allowlisted users is vulnerable, and the attack is repeatable by any unprivileged caller.

## Recommendation

Pass the actual initiating user's address through the swap path so the extension can gate on it. The preferred approach is to have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it instead of (or in addition to) `sender`. This requires no core changes. The `DepositAllowlistExtension` pattern — checking the economically relevant party (`owner`) rather than the immediate caller (`sender`) — is the correct model.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is permitted
  allowedSwapper[pool][router] = true  // admin enables router-based swaps for alice

Attack:
  bob (not on allowlist) calls:
    router.exactInputSingle({pool: pool, ...})
      → pool.swap(recipient, ...) with msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true → PASSES
  bob's swap executes against the pool despite not being on the allowlist.

Result:
  The per-user allowlist is bypassed for all router users.
  Alice's individual allowlist entry is irrelevant — bob achieves the same access.
```

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
