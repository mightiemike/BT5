Audit Report

## Title
`SwapAllowlistExtension` checks the immediate `pool.swap()` caller instead of the end-user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` populates with its own `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is that immediate caller, so the extension checks the router's address rather than the real user's address. If the pool admin allowlists the router (the natural step to enable router-based access), every unprivileged user can bypass the allowlist by calling the same public router.

## Finding Description
**Root cause — three confirmed code facts:**

1. `MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

2. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no user-identity forwarding — the router is `msg.sender` of that call: [3](#0-2) 

**Exploit flow:**
```
attacker → MetricOmmSimpleRouter.exactInputSingle()
             → pool.swap(recipient, ...) [msg.sender = router]
                  → _beforeSwap(sender = router, ...)
                       → allowedSwapper[pool][router] → true → passes
```

The pool admin has no mechanism to simultaneously allowlist the router for intended users while blocking non-allowlisted users from using the same router. Allowlisting the router collapses the allowlist to "anyone who calls the router," which is every user. No existing guard in `SwapAllowlistExtension` inspects `extensionData` for a real user identity, and the router passes `params.extensionData` through without encoding the caller.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., approved market makers, KYC'd addresses, or protocol-internal actors) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. This is a confirmed admin-boundary break: an unprivileged path defeats a configured access guard, allowing unauthorized traders to execute swaps against a pool whose LP positions were sized under the assumption that only vetted counterparties would trade. This causes adverse selection against LPs and violates the pool's intended access policy.

## Likelihood Explanation
The scenario requires only one intuitive admin action: calling `setAllowedToSwap(pool, router, true)` so that allowlisted users can reach the pool via the standard router. Nothing in the `SwapAllowlistExtension` interface, NatSpec, or `setAllowedToSwap` setter warns that this collapses the allowlist for all users. [4](#0-3) 

No attacker capability beyond calling the public router is required. The bypass is repeatable on every swap.

## Recommendation
The extension must resolve the actual end-user identity rather than the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires a trusted encoding convention between router and extension.
2. **Router-aware check**: The extension inspects whether `sender` is a known router and, if so, reads the real user from a verified field in `extensionData`.
3. **Minimal mitigation (documentation)**: Document that the router must never be allowlisted and that allowlisted users must call `pool.swap()` directly. This breaks router UX for curated pools but eliminates the bypass.

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
