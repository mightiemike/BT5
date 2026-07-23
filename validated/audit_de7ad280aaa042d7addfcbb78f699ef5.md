Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address. If the pool admin allowlists the router to enable router-based swaps for any permitted user, every unprivileged address can bypass the per-user allowlist by calling the router. This silently nullifies the admin's intended access control for all router-mediated swaps.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
``` [2](#0-1) 

In all four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), the router itself calls `pool.swap()`, making the router the `msg.sender` seen by the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][end_user]`. The original caller's address is stored in transient storage via `_setNextCallbackContext` for payment purposes only — it is never forwarded to the extension. The `extensionData` field is passed through from the caller's input unchanged, so there is no trusted mechanism to supply the real caller to the extension. [4](#0-3) 

## Impact Explanation
Any address can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter`. The pool admin's intended access control (KYC gating, institutional-only pools, compliance restrictions) is silently nullified for all router-mediated swaps. This is a direct admin-boundary break: an unprivileged public path (`MetricOmmSimpleRouter`) causes the extension guard to authorize actors the admin explicitly did not permit, enabling unauthorized swaps against a pool that should be access-controlled.

## Likelihood Explanation
The admin must allowlist the router to enable router-based swaps for any permitted user. This is a natural and expected operational step — without it, permitted users cannot use the standard periphery. Once the router is allowlisted, the bypass is immediately available to every address with no further preconditions. The attacker needs only to call `router.exactInputSingle()` (or any other router entry point) targeting the pool.

## Recommendation
`SwapAllowlistExtension.beforeSwap` must gate on the end user, not the direct caller of `pool.swap()`. The cleanest fix is for the router to ABI-encode the original `msg.sender` into `extensionData` before forwarding it to `pool.swap()`, and for the extension to decode and verify it. To prevent spoofing, the extension must verify the caller (`msg.sender` in `beforeSwap`, i.e., the pool) is associated with a trusted router — for example, via a factory-attested router registry. Alternatively, the pool could expose the payer from its callback context so the extension can read it directly without relying on caller-supplied data.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, Alice, true)      // Alice is permitted
  admin calls setAllowedToSwap(pool, router, true)     // router allowlisted to enable Alice's router swaps
  Mallory is NOT allowlisted

Attack:
  Mallory calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...) with msg.sender = router
  → pool calls _beforeSwap(router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for Mallory despite her not being individually permitted

Direct call (correctly blocked):
  Mallory calls pool.swap(...) directly
  → pool calls _beforeSwap(Mallory, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][Mallory] == false  ✗
  → revert NotAllowedToSwap
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
