Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any Caller to Bypass Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap` call — the direct caller of the pool, not the originating user. When swaps are routed through `MetricOmmSimpleRouter`, the router contract is the direct pool caller, so `sender` is always the router's address. Any pool admin who allowlists the router (the only way to let intended users trade through it) simultaneously grants unrestricted swap access to every address on-chain, completely defeating the per-user gate.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces access control by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the address passed in from the pool: [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of the pool) as the `sender` argument to `_beforeSwap`: [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router itself calls `pool.swap(...)` directly, making the router contract `msg.sender` inside the pool: [3](#0-2) 

The same applies to `exactInput` (multi-hop): [4](#0-3) 

And `exactOutputSingle` and `exactOutput`: [5](#0-4) [6](#0-5) 

Because the router is the direct caller of `pool.swap`, `sender` inside `beforeSwap` is always the router's address — never the actual end user. The pool admin faces an impossible choice: do not allowlist the router (allowlisted users cannot use the router at all, their swaps revert because `allowedSwapper[pool][router]` is false), or allowlist the router (every user on-chain can call any router swap function and the check passes, bypassing the per-user gate entirely). There is no configuration that simultaneously allows specific users to trade through the router while blocking others.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a private institutional pool, a KYC-gated pool, or a pool restricted to specific market makers) can be accessed by any unprivileged user via the public router. The unauthorized user executes swaps at oracle-derived prices, exposing LP positions to unrestricted adverse-selection flow that LPs explicitly opted out of. This is a broken access-control invariant with direct LP fund impact: LPs bear adverse-selection risk from unrestricted counterparties, constituting a direct loss of LP principal value above Sherlock thresholds.

## Likelihood Explanation
The bypass requires only that the pool admin allowlists the router — a natural and expected action for any pool that wants its allowlisted users to be able to use the standard periphery. The router is a public, permissionless contract. Once the router is allowlisted, any address can call it with no special privileges, no flash loan, no malicious token, and no privileged setup. The condition is trivially reachable in any real deployment.

## Recommendation
Pass the actual end-user identity through the hook rather than the direct pool caller. Two viable approaches:

1. **Extend `extensionData` with the originating user**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. The extension must verify the attested identity cannot be spoofed by a caller who constructs `extensionData` directly (e.g., by requiring the pool to validate the encoding or by using a trusted router registry).
2. **Standardized originator field**: Add a standardized "originator" field to the extension interface so the router can attest the real user, and the allowlist verifies the attested identity, with the pool enforcing that only trusted routers may set this field.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // intended: only alice may swap
  allowedSwapper[pool][router] = true   // admin adds this so alice can use the router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  Execution path:
    charlie → router.exactInputSingle(...)
    router  → pool.swap(...)          // msg.sender in pool = router
      _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router, ...)
          allowedSwapper[pool][router] == true  → check passes
      swap executes at oracle price
      charlie receives token output

Result:
  charlie, who is not in the allowlist, successfully swaps in a
  pool configured to be restricted to alice only.
  LPs are exposed to unrestricted counterparty flow they opted out of.
```

A Foundry integration test can confirm this by deploying a pool with `SwapAllowlistExtension`, setting `allowedSwapper[pool][alice] = true` and `allowedSwapper[pool][router] = true`, then calling `router.exactInputSingle` from an address that is not alice and verifying the swap succeeds.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
