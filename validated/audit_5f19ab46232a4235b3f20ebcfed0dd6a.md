Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address rather than the end user. If the pool admin allowlists the router to enable router-mediated swaps for their KYC'd users — a natural, non-malicious action — every unprivileged address can bypass the allowlist entirely by routing through the router.

## Finding Description
**Root cause — sender binding in `MetricOmmPool.swap`:**
The pool passes `msg.sender` of `pool.swap()` as the `sender` argument to every before-swap extension: [1](#0-0) 

**Router call — `MetricOmmSimpleRouter.exactInputSingle`:**
The router calls `pool.swap()` directly, making `msg.sender` inside the pool the router contract, not the end user: [2](#0-1) 

**Guard check — `SwapAllowlistExtension.beforeSwap`:**
The extension receives `sender = router` and evaluates `allowedSwapper[msg.sender][router]` (where `msg.sender` is the pool): [3](#0-2) 

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists KYC'd users via `setAllowedToSwap(pool, user_A, true)`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so allowlisted users can swap through the router.
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` — `msg.sender` inside the pool is the router.
5. Pool dispatches `_beforeSwap(sender=router, ...)` to the extension.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
7. Attacker completes the swap in the restricted pool. `allowedSwapper[pool][attacker]` is never consulted.

**Structural consequence without the router allowlisted:**
If the admin does not allowlist the router, allowlisted users cannot use the router at all — the extension reverts on `allowedSwapper[pool][router] == false`. The extension forces a binary choice: either the router is allowlisted (bypass) or the router is unusable for the pool (broken functionality).

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-gated, institutional-only) is fully open to any caller who routes through `MetricOmmSimpleRouter` once the router is allowlisted. This constitutes an admin-boundary break: the pool admin's intended access-control invariant (`allowedSwapper[pool][actual_end_user]`) is never enforced on the router path. Unauthorized swaps drain pool liquidity and violate the access-control invariant the pool admin intended to enforce, constituting a direct loss of pool assets and broken core pool functionality.

## Likelihood Explanation
The trigger is a non-malicious, expected pool-admin action: allowlisting the router so that allowlisted users can benefit from router-mediated swaps. The admin has no on-chain signal that this action opens the pool to all users. Any pool that combines `SwapAllowlistExtension` with router support is affected. The attacker requires no special privileges — only the ability to call `MetricOmmSimpleRouter.exactInputSingle`, which is a public function.

## Recommendation
The extension must gate on the actual end user, not the direct caller. Two options:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` (the end user) into `extensionData` and have the extension decode and check it. This requires a coordinated convention between router and extension.
2. **Trusted router registry**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

The simplest safe fix is to remove router support from the allowlist model entirely and require end users to call `pool.swap()` directly when the allowlist extension is active.

## Proof of Concept
```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Admin allowlists router so allowlisted users can use it
vm.prank(poolAdmin);
ext.setAllowedToSwap(pool, address(router), true);

// Attacker (not individually allowlisted) routes through the router
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: attacker,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds — allowlist bypassed
// allowedSwapper[pool][router] == true was checked, not allowedSwapper[pool][attacker]
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
