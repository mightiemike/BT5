All four cited files are confirmed in the repository. The code matches the claim exactly:

- `MetricOmmPool.sol` L230-231: passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling.sol` L149-177: forwards `sender` verbatim to every extension [2](#0-1) 
- `SwapAllowlistExtension.sol` L37: checks `allowedSwapper[msg.sender][sender]` — pool is `msg.sender`, router is `sender` [3](#0-2) 
- `MetricOmmSimpleRouter.sol` L72-80: router calls `pool.swap()` directly with no originator encoding [4](#0-3) 

The exploit path is fully reachable and the impossible-choice dilemma for pool admins is real. The finding is valid.

---

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is the pool's own `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any non-allowlisted user can bypass a per-user swap allowlist on a curated pool by routing through the public router.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap` (L230–231). `ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension via `abi.encodeCall` (L149–177). `SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` (L37).

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(...)` directly (L72–80). At that point the pool's `msg.sender` is the router, not the original user. The extension therefore checks `allowedSwapper[pool][router]`.

This creates an impossible choice for the pool admin:
- **Do not allowlist the router**: allowlisted users cannot use the router at all.
- **Allowlist the router**: every user, including non-allowlisted ones, bypasses the guard, because `allowedSwapper[pool][router] == true` passes for all callers regardless of who initiated the transaction.

There is no configuration that simultaneously allows router-mediated swaps and enforces per-user allowlisting. The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` — an explicit parameter passed by the caller — rather than on `sender` (the immediate caller of the pool action).

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC-gated, institutional, or whitelist-only pools) can be fully bypassed by any public user routing through `MetricOmmSimpleRouter`. The allowlist protection — the pool's primary access-control mechanism — fails open for all router-mediated swaps. Unauthorized users can execute swaps on pools designed to exclude them, directly impacting LP providers who configured the allowlist to control counterparty exposure. This constitutes a High-severity direct policy bypass with fund-impacting consequences.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps. Any user who reads the protocol documentation will naturally route through it. No special knowledge, privileged role, flash loan, or unusual setup is required — the bypass is the default path for any non-allowlisted user who wants to trade on a restricted pool. The attack is repeatable on every router-mediated swap.

## Recommendation
Pass the original initiator of the swap through to the extension rather than the immediate caller of `pool.swap`. Two concrete approaches:

1. **Encode the original sender in `extensionData`**: The router appends `msg.sender` (the actual user) to `extensionData` before forwarding it to the pool. The extension decodes and checks that value. This requires a convention between router and extension but requires no core changes.

2. **Add an `originator` field to the swap interface**: Extend `pool.swap` with an explicit `originator` parameter that the router populates with its own `msg.sender`. The extension checks `originator` instead of `sender`. The pool must validate that `originator == msg.sender` when called directly (not through a trusted router).

## Proof of Concept
```
Setup:
  1. Deploy a pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only Alice is allowed.
  3. Pool admin calls setAllowedToSwap(pool, router, true) — router must be allowlisted
     so that Alice can also use the router (otherwise Alice cannot route either).

Attack:
  4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(recipient, ...) — pool's msg.sender = router.
  6. Pool calls _beforeSwap(sender=router, ...).
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  8. Bob's swap executes on the curated pool despite never being allowlisted.

Result:
  Bob successfully trades on a pool that was supposed to exclude him.
  If the admin does NOT allowlist the router (step 3), Alice also cannot use the router,
  so the admin cannot grant router access to Alice without granting it to everyone.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-37)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
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
