Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of actual user, allowing full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address. A pool admin who allowlists the router to enable normal UX simultaneously opens the pool to every user on the network, completely defeating the curation policy.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool see `msg.sender = router`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an impossible choice: do not allowlist the router (no user can swap via router, even individually allowlisted ones), or allowlist the router (every user bypasses the allowlist). There is no third option in the current design.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` — the actual position owner passed by the pool itself — not `sender` (the LiquidityAdder intermediary): [5](#0-4) 

The swap path has no equivalent "payer" or "actual user" parameter threaded through from the pool, making the structural fix non-trivial.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd counterparties or whitelisted market makers is completely defeated once the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and execute swaps at oracle-anchored prices. This constitutes a direct bypass of the pool admin's curation policy and can result in LP value leakage to unpermissioned actors at oracle-fair prices the pool admin intended to restrict — a broken core pool functionality and admin-boundary break by an unprivileged path.

## Likelihood Explanation
The router is the canonical periphery path for swaps. Any pool admin who wants allowlisted users to use the router (the normal UX) must allowlist the router address, which immediately opens the pool to all users. The misconfiguration is the only viable operational path, making exploitation trivially reachable by any user who calls the router with the curated pool address.

## Recommendation
The `beforeSwap` hook must not rely on `sender` (the direct caller of `pool.swap()`) to identify the economic actor. Two options:

1. **Check `recipient` as a proxy** — only valid if the pool admin's intent is to restrict who receives output, not who pays input.
2. **Require actual user identity in `extensionData`** — the router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated change to the router and extension, and the extension must trust that the router correctly encodes the real caller.

The correct long-term fix is to thread an explicit "payer" or "originator" field through the swap path analogous to how `owner` is threaded through the liquidity path, so the pool itself controls and sets the actual economic actor identity.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is supposed to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The pool calls `extension.beforeSwap(router, bob, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. Bob's swap succeeds.
6. The allowlist is fully bypassed; Bob receives output tokens from the curated pool.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
