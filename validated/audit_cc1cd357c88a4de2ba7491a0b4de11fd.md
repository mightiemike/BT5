Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool level is the router contract, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the network can bypass the per-user allowlist by routing through the public router.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions.**

`MetricOmmPool.swap()` unconditionally passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

**Step 2 — `_beforeSwap` forwards `sender` verbatim to all configured extensions.**

`ExtensionCalling._beforeSwap` encodes `sender` and passes it to every extension in order: [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` gates on `sender`.**

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first parameter — the router address when routing through `MetricOmmSimpleRouter`: [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself `msg.sender`.**

In `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`, the router calls `IMetricOmmPoolActions(pool).swap(...)` directly: [4](#0-3) [5](#0-4) 

At the pool level, `msg.sender` is the router contract. The extension receives `sender = router`, not the end user.

**Step 5 — Binary dilemma for pool admin.**

| Admin choice | Effect |
|---|---|
| Allowlist the router | Every user bypasses the individual allowlist via the public router |
| Do NOT allowlist the router | Individually allowlisted users cannot use the router at all |

There is no configuration that simultaneously enforces per-user allowlisting and permits router-mediated swaps.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties is completely defeated. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInput()` or `exactOutput()` targeting the curated pool. The router's address passes the allowlist check, the swap executes, and the user receives pool output tokens. This constitutes a direct policy bypass with fund-impacting consequences: disallowed users trade against LP capital deposited under the assumption that only approved counterparties could access the pool. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation

Likelihood is high. The attack requires no special permissions, no flash loans, and no privileged access. Any user who can call the public router can exploit this. The only precondition is that the pool admin has allowlisted the router, which is the natural operational choice to support normal trading UX. The bypass is deterministic and repeatable on every block.

## Recommendation

The `sender` forwarded to extensions must represent the economically relevant actor, not the intermediary contract. Two concrete fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the original `msg.sender` (the end user) into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when `extensionData` is non-empty.

2. **Extension-side**: `SwapAllowlistExtension` should expose a secondary allowlist keyed on `recipient` and check it when `sender` is a known router, or require `sender == recipient` for direct-user enforcement.

The core invariant to enforce: the identity checked by the allowlist must be the same actor to whom the economic output of the swap is attributed.

## Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is the only allowed swapper.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router must be allowlisted for UX.
4. Bob (not allowlisted) calls router.exactInput({pools: [pool], ...}).
5. Router calls pool.swap(recipient=bob, ...) — msg.sender at pool = router.
6. _beforeSwap forwards sender=router to SwapAllowlistExtension.
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes against LP capital; the allowlist is bypassed.

Direct call by Bob (pool.swap() directly) would correctly revert:
  allowedSwapper[pool][bob] == false → NotAllowedToSwap.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
