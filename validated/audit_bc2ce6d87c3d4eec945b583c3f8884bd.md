Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call — the router contract, not the end user. When a pool admin allowlists the router to enable router-mediated swaps for legitimate users, every non-allowlisted user can bypass the restriction by routing through the same public router. The allowlist access-control invariant is fully broken.

## Finding Description
**Pool side:** `MetricOmmPool.swap` passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`: [1](#0-0) 

**Extension dispatch:** `ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

**Guard check:** `SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool: [3](#0-2) 

**The mismatch:** When any user calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`, the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool's `msg.sender` is the **router**, so the extension receives `sender = router_address` and evaluates `allowedSwapper[pool][router_address]`. Allowlisting the router is the only way to make the router usable for legitimate users, but doing so opens the allowlist to the entire public. The `extensionData` bytes are passed through but `SwapAllowlistExtension` ignores them entirely — there is no mechanism to convey the original user.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter`. The attacker receives the same swap output as an allowlisted user, executing trades the pool designer explicitly prohibited. This is a direct break of the pool's intended access-control invariant with fund-impacting consequences for LPs — matching the "admin-boundary break by an unprivileged path" and "broken core pool functionality causing loss of funds" allowed impact categories.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entrypoint deployed alongside the core pool. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which immediately opens the bypass to all users. The trigger requires no privileged access, no special token behavior, and no unusual timing — a single public `exactInputSingle` call suffices. The condition (router allowlisted) is the normal operational state for any pool that intends to support router-mediated swaps.

## Recommendation
Pass the **original user** through the call chain rather than the intermediary. Two complementary approaches:

1. **Router-side:** Have `MetricOmmSimpleRouter` encode the original `msg.sender` inside `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it (trusting the outer `msg.sender` pool check already enforced via `onlyPool`/`msg.sender == pool`).

2. **Pool-side:** Add an optional `originator` parameter to `pool.swap` that the pool passes to extensions as a separate field, defaulting to `msg.sender` for direct calls. Extensions gate on `originator` instead of `sender`.

Either way, the extension must gate on the **economically responsible actor** (the user whose funds are being moved), not the intermediary contract relaying the call.

## Proof of Concept
```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  3. Admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack (bob, not allowlisted):
  4. bob calls MetricOmmSimpleRouter.exactInputSingle(
         { pool, tokenIn, tokenOut, amountIn, minOut, recipient: bob, ... }
     )
  5. Router calls pool.swap(bob, zeroForOne, amountIn, priceLimit, "", extensionData)
     → pool's msg.sender = router
  6. Pool calls _beforeSwap(sender=router, ...)
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE (step 3)
  8. Swap executes; bob receives token output.

Result: bob, who is not on the allowlist, successfully swaps on a curated pool.
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
