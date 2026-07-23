Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end-user. If the router is allowlisted for a pool, every user on the network can bypass the per-user swap allowlist by routing through the public router contract, exposing LP funds to unauthorized counterparties.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim into the encoded call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) 

The allowlist lookup becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`. A pool admin who allowlists the router to enable router-based trading simultaneously opens the pool to all users, regardless of individual allowlist status. No existing guard in the extension or the router resolves the original end-user identity.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, protocol-owned addresses, or whitelisted market makers) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter`. The attacker executes arbitrary swaps against the restricted pool without being individually allowlisted, exposing LP funds to unauthorized counterparties and breaking the core pool invariant that the allowlist guard is meant to enforce. This constitutes a direct loss of LP assets.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public entry point for swaps in the periphery layer. Pool admins who want to allow router-based trading for their allowlisted users must allowlist the router — a natural, expected operational step — which simultaneously opens the pool to all users. No special privilege or setup is required from the attacker beyond calling the public router. The misconfiguration is not an exotic edge case; it is the default operational path.

## Recommendation

The extension must resolve the original end-user identity, not the direct caller of `pool.swap`. Options:

1. **Pass the original sender via `extensionData`**: `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` before calling `pool.swap`. `SwapAllowlistExtension.beforeSwap` decodes the original sender from `extensionData` and verifies it against `tx.origin` or a signed proof, then checks `allowedSwapper[pool][originalSender]`.
2. **Separate router allowlisting from user allowlisting**: Provide a router wrapper that enforces its own per-user allowlist before calling the pool, and document clearly that allowlisting the router opens the pool to all users.
3. **Check `tx.origin` as a fallback**: When `sender` is a known router contract, fall back to `tx.origin`. Simpler but introduces `tx.origin` risks in contract-wallet contexts.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(...) — msg.sender inside pool = router address
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true → PASSES
  - Attacker's swap executes against the restricted pool

Result:
  - Attacker (not individually allowlisted) successfully swaps against a pool
    restricted to specific addresses only.
  - LP funds are exposed to an unauthorized counterparty.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
