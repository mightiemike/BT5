Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Pool Access Control — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool and is forwarded as `sender` to the extension. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the gate to every user on the network, nullifying the allowlist entirely.

## Finding Description
The call path is as follows:

1. `MetricOmmSimpleRouter.exactInputSingle()` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly — making the router contract `msg.sender` to the pool. [1](#0-0) 

2. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`. [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap()` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — the original EOA is never seen. [3](#0-2) 

There is no mechanism in the extension, pool, or router to propagate the original caller's identity. The `extensionData` field passed by the router is user-controlled (`params.extensionData` forwarded verbatim), so it cannot be trusted as an authenticated identity source. [4](#0-3) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties or protocol-controlled addresses). Once the pool admin allowlists the router — which is required for any legitimate user to swap through the standard periphery — the allowlist is effectively nullified. Any unprivileged user can call `exactInputSingle` on the public, permissionless `MetricOmmSimpleRouter` and execute swaps against the restricted pool. This is an admin-boundary break: the pool admin's access-control invariant is bypassed by an unprivileged path, and any LP principal deposited under the assumption of a restricted pool is exposed to unrestricted trading.

## Likelihood Explanation
The bypass is trivially reachable. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user who observes that the router is allowlisted on a pool (readable from `allowedSwapper[pool][router]`) can immediately route through it. The pool admin has no mechanism to allowlist the router for some users but not others — the router is a single address. The only way to avoid the bypass is to never allowlist the router, which forces all legitimate users to call the pool directly and defeats the purpose of the periphery.

## Recommendation
The `SwapAllowlistExtension` should gate on the original user identity, not the intermediary. The cleanest fix is for the router to encode `abi.encode(msg.sender)` into `extensionData` for allowlist-gated pools, and for the extension to decode and check that value when `sender` is a known/trusted router. Alternatively, the pool interface could be extended to carry an explicit `payer` field distinct from `sender`, populated by the router with the authenticated original caller before dispatching to the pool.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    to enable router-mediated swaps for legitimate users.
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({
        pool: pool,
        tokenIn: token0,
        tokenOut: token1,
        amountIn: X,
        recipient: attacker,
        ...
      })

  - router calls pool.swap(attacker, zeroForOne, X, ...)
      → msg.sender to pool = router
  - pool calls _beforeSwap(router, attacker, ...)
  - extension checks allowedSwapper[pool][router] → true ✓
  - swap executes; attacker receives token1 output.

Result:
  attacker bypasses the allowlist and swaps on a curated pool.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
