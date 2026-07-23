Audit Report

## Title
Router-mediated swaps substitute the router address for the original user in `SwapAllowlistExtension.beforeSwap`, breaking per-user allowlist enforcement — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the originating user. This breaks the allowlist in both directions: allowlisted users are blocked when using the router, and if the router itself is allowlisted, any user bypasses the per-user gate.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap` at [1](#0-0) , which forwards it unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap` uses this value as the swapper identity to check against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, `msg.sender` inside the pool is the router contract, not the end user: [3](#0-2) 

The router stores the original caller only in transient storage for the payment callback (`_setNextCallbackContext(..., msg.sender, ...)`) and never passes it to the pool as an identity parameter. Consequently, the extension sees `sender = router` for every router-mediated swap. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The `whenNotPaused` guard on `swap` reverts before `_beforeSwap` is reached only when the pool is paused, so it provides no mitigation for the identity mismatch in the normal (unpaused) flow: [4](#0-3) 

## Impact Explanation
- **Broken core functionality (certain):** Any pool using `SwapAllowlistExtension` with a non-`allowAll` configuration is unusable via the router for allowlisted users. Allowlisted users who attempt to swap through the router receive `NotAllowedToSwap` reverts, making the primary user-facing swap path non-functional.
- **Admin-boundary break / allowlist bypass (conditional but unprivileged):** If the pool admin allowlists the router address — a natural operational step to permit router access — every unprivileged user can bypass the per-user allowlist by routing through the router. The bypass requires no special privilege beyond calling the public router.

## Likelihood Explanation
The router is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` with per-user restrictions and expects users to interact via the router will immediately exhibit the broken-functionality impact. The bypass scenario follows directly from the natural admin response to the first impact (allowlisting the router). Both effects are reachable by any unprivileged caller with no preconditions beyond a deployed pool with this extension.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should not rely solely on the `sender` argument for identity. Options:
1. Have the router encode the original user's address in `extensionData`, and have the extension decode and verify it against a trusted router registry.
2. Add a router-aware path where the router is a trusted forwarder that encodes the real user in `extensionData`, and the extension verifies the router's identity before trusting the forwarded address.
3. Alternatively, the pool/extension framework could propagate a verified originator alongside `sender`, though `tx.origin` has its own risks and should be avoided.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension; set allowAllSwappers[pool] = false.
2. Call setAllowedToSwap(pool, alice, true).
3. Alice calls router.exactInputSingle({pool: pool, ...}) — reverts with NotAllowedToSwap
   because pool sees msg.sender = router, and allowedSwapper[pool][router] = false.

4. Admin calls setAllowedToSwap(pool, router, true) to "fix" router access.
5. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...}) — succeeds,
   because allowedSwapper[pool][router] = true covers all router callers.
   Bob has bypassed the per-user allowlist.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
