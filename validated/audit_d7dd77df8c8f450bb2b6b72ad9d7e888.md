Audit Report

## Title
Swap Allowlist Checks Router Address Instead of End User, Allowing Any User to Bypass the Gate via the Standard Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address — not the actual end user — because `MetricOmmPool.swap` passes `msg.sender` directly. Any pool admin who allowlists the router (required for router-mediated swaps to function) simultaneously opens the gate to every address on the network, completely defeating the allowlist.

## Finding Description

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`:**

`MetricOmmPool.swap` at lines 230–231 passes `msg.sender` as the first argument to `_beforeSwap`. When the call originates from `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the end user. [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension:**

`ExtensionCalling._beforeSwap` at lines 160–176 encodes `sender` (the router address) into the call to each extension without modification. [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `sender` (the router), not the actual user:**

The allowlist check at line 37 is `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router. The actual end user who called `exactInputSingle` is never visible here. [3](#0-2) 

**Step 4 — The router never forwards the original caller to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` stores `msg.sender` in transient storage at line 71 for the payment callback only, but calls `pool.swap` at lines 72–80 with no originator argument. The pool sees `msg.sender == router`. [4](#0-3) 

**Root cause:** The pool's `swap` interface has no `originator` parameter. The only identity information passed to extensions is `msg.sender` of the pool call, which is the router when the standard periphery is used. The same structural flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap` as `msg.sender == router`. [5](#0-4) [6](#0-5) 

**Existing guards are insufficient:** The `allowAllSwappers` flag is a separate escape hatch; it does not fix the per-address check. There is no on-chain mechanism in `SwapAllowlistExtension` to distinguish a router call from a direct call, nor to recover the original caller's identity. [7](#0-6) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC-verified users, protocol-internal actors, or whitelisted market makers). Once the router is allowlisted — the only way to let any allowlisted user trade through the standard periphery — the gate is open to the entire public. Non-allowlisted users can execute swaps at oracle-anchored prices, draining LP value or extracting arbitrage that the pool's curation policy was designed to prevent. This is a direct loss of LP principal and a broken core pool invariant (curated access). The corrupted value is the `allowedSwapper[pool][sender]` extension decision: it evaluates `true` for the router when it should evaluate `true` only for individually approved end users.

## Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) expects users to interact through `MetricOmmSimpleRouter` is immediately vulnerable. The router is the standard, documented periphery entry point. A pool admin who follows the normal integration path will allowlist the router, unknowingly opening the bypass. The attacker requires no special role, no privileged setup, and no non-standard token — a single call to `exactInputSingle` suffices. The condition is trivially reachable and repeatable by any address.

## Recommendation

1. **Pass the original caller through the router.** The router should forward `msg.sender` to the pool (e.g., via a dedicated `originator` field in the swap call or encoded in `callbackData`), and the pool should pass it as a separate argument to extensions.
2. **Check `originator` in the allowlist extension.** `SwapAllowlistExtension.beforeSwap` should check the originator address when the immediate `sender` is a known periphery contract, or always check the originator when it is provided.
3. **Short-term mitigation:** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call the pool directly. This is a severe UX restriction that underscores the need for the structural fix.

## Proof of Concept

```
Setup:
  - Pool P deployed with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, alice, true)   // alice is KYC'd
  - Pool admin calls E.setAllowedToSwap(P, router, true)  // needed for alice to use router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, ...) — msg.sender at pool = router
  3. Pool calls _beforeSwap(router, ...)
  4. Extension checks allowedSwapper[P][router] → true
  5. Swap executes for bob with no revert

Result:
  bob trades on a curated pool that was supposed to block him,
  bypassing the allowlist entirely.

Foundry test outline:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][router] = true; allowedSwapper[pool][bob] = false
  - vm.prank(bob); router.exactInputSingle(...)
  - Assert: no revert, swap succeeds
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
