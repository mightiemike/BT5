Audit Report

## Title
SwapAllowlistExtension Bypassed via Router When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `MetricOmmPool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the end user. A pool admin who allowlists the router so that their allowlisted users can use it inadvertently grants swap access to every caller of the router, completely defeating the allowlist.

## Finding Description

**Step 1 — Pool forwards `msg.sender` as `sender` to the extension:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

**Step 2 — Extension checks `allowedSwapper[pool][sender]`:**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the pool key and `sender` (the pool's caller) as the swapper: [2](#0-1) 

**Step 3 — Router calls `pool.swap()` as itself:**

`exactInputSingle` stores the true payer in transient storage via `_setNextCallbackContext` but calls `pool.swap()` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The true end-user address (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the pool or the extension. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**Step 4 — The bypass:**

When the pool admin allowlists the router address (a natural step to let their allowlisted users use the standard periphery entry point), `allowedSwapper[pool][router] == true`. The extension then passes for any caller of the router, regardless of whether that caller is on the allowlist. The extension has no mechanism to recover the true end user from transient storage.

## Impact Explanation

The `SwapAllowlistExtension` is a core access-control extension designed to restrict swap access to specific addresses per pool (e.g., KYC-gated pools, institutional-only pools, or pools with favorable pricing for specific counterparties). When the router is allowlisted, the extension's gate is completely bypassed for all router callers. Non-allowlisted users gain full swap access to pools designed to exclude them. This constitutes broken core pool functionality — the extension's primary invariant (`only allowlisted addresses may swap`) fails entirely under a natural and expected configuration.

## Likelihood Explanation

The router is the standard periphery entry point for end users. A pool admin who wants their allowlisted users to be able to use the router must allowlist the router address — this is a completely reasonable and expected operational step. Once done, the bypass is available to any public user with no special privileges, no malicious setup, and no non-standard token behavior. The attacker only needs to call `router.exactInputSingle` (or any other router entry point) targeting the restricted pool.

## Recommendation

The extension must verify the economic actor (the entity providing tokens), not the immediate caller. Options:

1. **Pass the true payer through `extensionData`**: The router encodes the true payer (`msg.sender` of the router call) in `extensionData`; the extension reads and verifies it against the allowlist. Requires a convention between router and extension.
2. **Trusted forwarder concept**: The extension maps trusted intermediaries (e.g., the router) to a "read payer from `extensionData`" mode, verifying the payer address encoded there against the allowlist.
3. **Document and enforce direct-call-only**: Document that the allowlist only works for direct pool calls and that the router must never be allowlisted. This is a weak mitigation since it relies on admin awareness of a non-obvious footgun.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true       // alice is KYC'd
  allowedSwapper[pool][router] = true      // admin allowlists router so alice can use it
  allowedSwapper[pool][attacker] = false   // attacker is NOT allowlisted

Attack:
  attacker calls router.exactInputSingle({pool: pool, recipient: attacker, ...})
  → router calls pool.swap(attacker, ...)  [router is msg.sender to pool]
  → pool calls _beforeSwap(msg.sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for attacker despite attacker not being allowlisted

Assert:
  attacker successfully swaps on a pool they should be excluded from.
  The allowlist is completely bypassed.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][router] = true`, call `router.exactInputSingle` from an address not in the allowlist, assert the swap succeeds and `NotAllowedToSwap` is never reverted.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
