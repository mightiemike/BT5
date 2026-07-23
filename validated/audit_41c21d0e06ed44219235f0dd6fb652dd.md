Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the real user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of its own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the **router's address** against the allowlist, not the actual end-user. A pool admin who allowlists the router to enable router-based access for their approved users simultaneously grants every unprivileged user the ability to bypass the allowlist by calling the same public router.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231, so whoever called `pool.swap()` is forwarded as `sender` to the extension. [1](#0-0) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` — `sender` is the immediate caller of `pool.swap()`, not the end-user:** [2](#0-1) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no user-identity forwarding — the router is `msg.sender` of the pool call:** [3](#0-2) 

**Exploit call chain:**
```
attacker → MetricOmmSimpleRouter.exactInputSingle()
             → pool.swap(recipient, ...) [msg.sender = router]
                  → _beforeSwap(msg.sender=router, ...)
                       → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                            → allowedSwapper[pool][router] → true → passes
```

The pool admin has no mechanism to simultaneously allowlist the router (so approved users can reach the pool via it) and block non-approved users from doing the same. Allowlisting the router collapses the allowlist to "anyone who calls the router," which is every user.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., approved market makers, KYC'd addresses, or protocol-internal actors) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. Once the router is allowlisted, the curation boundary is eliminated: unauthorized traders can execute swaps, causing adverse selection against LPs, violating the pool's intended access policy, and potentially draining value from a pool whose LP positions were sized under the assumption that only vetted counterparties would trade. This is a confirmed admin-boundary break where an unprivileged path defeats a configured guard.

## Likelihood Explanation

The scenario is realistic and requires no special attacker capability. A pool admin who deploys a curated pool and wants their allowlisted users to access it via the standard router will naturally call `setAllowedToSwap(pool, router, true)`. Nothing in the interface, NatSpec, or documentation warns that this collapses the allowlist for all users. The bypass requires only that the pool admin takes this one intuitive configuration step; the attacker only needs to call the public router. [4](#0-3) 

## Recommendation

The extension must resolve the actual end-user identity rather than the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention and the extension knowing which routers are trusted forwarders.
2. **Document that the router must never be allowlisted and that allowlisted users must call `pool.swap()` directly**: This is the minimal mitigation but breaks router UX for curated pools.

The cleanest fix is approach 1: the router encodes the originating user into `extensionData`, and the extension, when it detects `sender` is a known trusted router, reads and checks the decoded user address instead.

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
