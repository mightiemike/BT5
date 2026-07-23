Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. If the router is allowlisted — which is required for any router-mediated swap to succeed — every unprivileged user can bypass the per-user allowlist by routing through the public router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool (`msg.sender` = pool): [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [3](#0-2) 

The pool's `msg.sender` is the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

A pool admin who wants to support router-mediated swaps for allowlisted users must allowlist the router address via `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of whether the actual end-user is on the allowlist. There is no existing guard that links the router's allowlist entry back to the identity of the real caller.

## Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd counterparties, specific market makers, or whitelisted protocols) must allowlist the router for any router-mediated swap to succeed. Once the router is allowlisted, every unprivileged user can call `exactInputSingle` or any other router entry point and execute swaps against the restricted pool. The allowlist is completely neutralized for the router path. This constitutes an admin-boundary break: an unprivileged path bypasses a pool admin's access control, allowing unauthorized users to execute swaps at oracle-derived prices, consuming LP liquidity and generating fees the pool admin intended to restrict to specific parties.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call it. Pool admins who want to support router-mediated swaps for their allowlisted users must allowlist the router, which simultaneously opens the bypass to all users. There is no configuration that allows router-mediated swaps for allowlisted users only while blocking non-allowlisted users — the two goals are mutually exclusive under the current design. Likelihood is high whenever a pool uses `SwapAllowlistExtension` and the router is allowlisted.

## Recommendation

The extension must gate on the actual end-user identity, not the immediate `msg.sender` of the pool call. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is trust-dependent.
2. **Redesign the extension interface**: Have the pool pass both the immediate caller and an optional "originator" field, and have the allowlist check the originator when present.
3. **Reject router-mediated swaps entirely**: Check `sender != router` and revert unless the router is redesigned to forward the real caller identity.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // Alice is allowlisted
  admin calls setAllowedToSwap(pool, router, true)      // router must be allowlisted for router swaps

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        zeroForOne: true,
        amountIn: X,
        recipient: bob,
        ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)          // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (bypass succeeds)
        → swap executes, bob receives output tokens

Result: Bob, who is not on the allowlist, successfully swaps against the restricted pool.
        The allowlist check passed because it checked the router's address, not Bob's.
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
