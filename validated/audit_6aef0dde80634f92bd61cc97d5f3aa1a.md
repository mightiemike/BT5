Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the end user. If the pool admin allowlists the router (required for any user to trade via the official periphery), every user — including those explicitly excluded — can bypass the allowlist by routing through the router.

## Finding Description

**Root cause in `SwapAllowlistExtension.beforeSwap`:**

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**What the pool passes as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at L230-231, where `msg.sender` is whoever called `pool.swap()`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← caller of pool.swap()
    ...
```

**What the router passes:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly at L72-80, making the router the `msg.sender` to the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The same applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165) — all call `pool.swap()` from the router context.

**The bypass:**

A pool admin who wants allowlisted users to trade via the official router must call `setAllowedToSwap(pool, router, true)`. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[pool][sender]` passes for every swap arriving through the router, regardless of who the actual end user is. Any address — including those explicitly excluded — can call `exactInputSingle` and trade on the restricted pool.

**No existing guard prevents this:** The extension has no mechanism to distinguish the router acting as an intermediary from the router acting as the actual swapper. The `extensionData` field is passed through but never decoded by the extension to recover the real caller.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers) is completely open to any user who routes through `MetricOmmSimpleRouter`. The allowlist provides no protection on the router path. This constitutes a broken core pool functionality causing direct loss of access control guarantees, and allows unauthorized users to execute swaps against the pool's liquidity at oracle-derived prices — draining LP value or executing trades the pool admin explicitly prohibited. This meets the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the official, documented swap interface for the protocol. Pool admins who deploy a curated pool and want their allowlisted users to trade will naturally allowlist the router. The bypass is then unconditional and requires no special setup by the attacker — any call to `exactInputSingle` on the router suffices. The only scenario where the bypass does not apply is if the pool admin never allowlists the router, in which case even allowlisted users cannot use the router (broken functionality). Both outcomes are harmful. Likelihood is high given the natural and expected configuration.

## Recommendation

The extension must gate the actual end user, not the intermediary. Two approaches:

1. **Forward the original caller through `extensionData`.** The router already stores the original `msg.sender` in transient storage via `_setNextCallbackContext`. Pass the original caller as an additional field in `extensionData` and have the extension decode and verify it. This requires coordinated changes to the router and extension.

2. **Trusted router attestation.** Maintain a registry of trusted routers in the extension. When `sender` is a trusted router, decode the attested real user from `extensionData` and check that address against the allowlist instead.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address.
  - Pool admin adds liquidity.

Attack:
  - attacker (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: pool,
          recipient: attacker,
          zeroForOne: true,
          amountIn: X,
          ...
      })

  - Router calls pool.swap(recipient=attacker, ...) with msg.sender = router.
  - Pool calls _beforeSwap(sender=router, ...).
  - Extension checks allowedSwapper[pool][router] → true → passes.
  - Swap executes. Attacker receives output tokens.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; allowlist is bypassed.
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
