Audit Report

## Title
SwapAllowlistExtension checks router address instead of originating user, allowing allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every unprivileged address can bypass the allowlist by calling through the router, exposing LPs to arbitrary toxic flow.

## Finding Description
The full call path is confirmed in production code:

**Step 1 — Router calls pool directly without forwarding the user:**
`MetricOmmSimpleRouter.exactInputSingle` stores the originating `msg.sender` only in transient storage for the payment callback via `_setNextCallbackContext`, then calls `pool.swap()` directly: [1](#0-0) 

The originating user address is never passed as an argument to `pool.swap()` or any extension.

**Step 2 — Pool passes `msg.sender` (the router) to `_beforeSwap`:** [2](#0-1) 

**Step 3 — `ExtensionCalling._beforeSwap` forwards `sender` (router address) verbatim to every configured extension:** [3](#0-2) 

**Step 4 — `SwapAllowlistExtension.beforeSwap` checks the received `sender` (router) against the per-pool allowlist:** [4](#0-3) 

`msg.sender` here is the pool (correct key namespace); `sender` is the router address when the swap enters via any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. There is no existing guard that recovers the originating user from transient storage inside the extension or the pool's swap path.

**The inescapable dilemma for pool admins:**
- Do **not** allowlist the router → allowlisted users cannot use the router at all.
- Allowlist the router → **every** address can bypass the allowlist via the router.

No configuration simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd addresses, protocol-owned bots, whitelisted market makers) is fully bypassed the moment the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool's LP positions. LPs who deposited under the assumption that only trusted counterparties would trade against them are exposed to arbitrary toxic flow, sandwich attacks, or oracle-price extraction from any address. This constitutes direct loss of LP principal through bad-price execution that the configured allowlist was supposed to prevent — matching the "bad-price execution" and "broken core pool functionality causing loss of funds" allowed impact categories.

## Likelihood Explanation
The trigger requires only that the pool admin allowlists the router — a natural and expected operational step for any curated pool that wants to support standard periphery tooling. No privileged escalation, no malicious setup, and no special token behavior is needed. Any user who can call `MetricOmmSimpleRouter` can exploit this immediately after the router is allowlisted. The condition is reachable by any unprivileged trader.

## Recommendation
Two remediation paths exist:

1. **Router-side**: Add an explicit `sender` parameter to the pool's `swap` interface (or a trusted-forwarder pattern) so the router can pass the originating user's address. The extension then checks that address instead of `msg.sender` of `pool.swap()`.

2. **Extension-side**: `SwapAllowlistExtension` should explicitly revert or document that it is incompatible with pools expected to receive swaps through the router unless `allowAllSwappers` is set. Alternatively, add a router-aware check that reads the originating user from a trusted forwarder context stored in transient storage.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin also allowlists the router so alice can use it.
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // needed for alice to use router

// Attack: bob (not allowlisted) calls the router directly.
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: bob,
        deadline: block.timestamp,
        zeroForOne: true,
        amountIn: 1000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Succeeds: extension sees sender=router (allowlisted), not bob (not allowlisted).
// Bob has bypassed the swap allowlist entirely.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
