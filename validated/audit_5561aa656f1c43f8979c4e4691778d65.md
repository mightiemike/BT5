Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the real user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` always sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address rather than the actual end-user. A pool admin who allowlists the router to let their intended users reach the pool via the standard router inadvertently grants every unprivileged user the same access, collapsing the allowlist to "anyone who calls the router."

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no user-identity forwarding — `msg.sender` of the pool call is the router contract, not the end user: [3](#0-2) 

The exploit path is:
```
Attacker → MetricOmmSimpleRouter.exactInputSingle()
  → pool.swap(recipient, ...) [msg.sender = router]
    → _beforeSwap(sender = router, ...)
      → SwapAllowlistExtension.beforeSwap(sender = router)
        → allowedSwapper[pool][router] == true → passes
```

There is no mechanism in the extension or the router to simultaneously (a) allowlist the router so intended users can reach the pool via it, and (b) block non-allowlisted users from doing the same. The `allowedSwapper` mapping has no per-user granularity once the router address is the key being checked. [4](#0-3) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., approved market makers, KYC'd addresses, or protocol-internal actors) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. Once the router is allowlisted, the curation boundary is eliminated: unauthorized traders can execute swaps, causing adverse selection against LPs whose positions were sized under the assumption that only vetted counterparties would trade. This is an admin-boundary break where an unprivileged path defeats a configured access guard, resulting in direct loss of value to LPs through adverse selection.

## Likelihood Explanation
The scenario is realistic and requires no privileged attacker capability. A pool admin who deploys a curated pool and wants their allowlisted users to access it via the standard router will naturally call `setAllowedToSwap(pool, router, true)`. Nothing in the interface, NatSpec, or the `setAllowedToSwap` setter warns that this collapses the allowlist for all users. [5](#0-4) 

The bypass requires only that the pool admin takes this one intuitive configuration step; the attacker then simply calls the public router.

## Recommendation
The extension must resolve the actual end-user identity rather than the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires a trusted encoding convention between router and extension.
2. **Maintain a router registry**: The extension checks whether `sender` is a known/trusted router and, if so, reads the real user from a verified field in `extensionData`.
3. **Document and enforce direct-call-only**: Document that the router must never be allowlisted and that allowlisted users must call `pool.swap()` directly. This is the minimal mitigation but breaks router UX for curated pools.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `trustedMM` is allowlisted.
swapExt.setAllowedToSwap(pool, trustedMM, true);
// Pool admin also allowlists the router so trustedMM can use it:
swapExt.setAllowedToSwap(pool, address(router), true);

// Attack: attacker (not allowlisted) routes through the public router.
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        tokenIn:          address(token0),
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1_000e18,
        amountOutMinimum: 0,
        recipient:        attacker,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// Extension sees sender = router → allowedSwapper[pool][router] = true → passes.
// Attacker swaps successfully despite not being on the allowlist.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
