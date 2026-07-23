All four files are confirmed in the repository. Let me verify the exact code matches the claim before rendering the final output.

**Verification summary:**

1. `MetricOmmPool.sol` L230-231: `_beforeSwap(msg.sender, ...)` — pool passes its own `msg.sender` (the router) as `sender`. ✓
2. `ExtensionCalling.sol` L162-165: `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` — forwarded unchanged. ✓
3. `SwapAllowlistExtension.sol` L37: `allowedSwapper[msg.sender][sender]` — checks `sender` (= router address) against the per-pool allowlist. ✓
4. `MetricOmmSimpleRouter.sol` L72-80: router calls `pool.swap(params.recipient, ...)` directly — `msg.sender` to pool is the router. ✓
5. No mechanism in the router encodes the original `msg.sender` into `extensionData`, and the extension does not decode `extensionData` to recover the true initiator. ✓

The exploit path is fully reachable and the code confirms both failure modes described in the claim.

---

Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router's address, not the end user's. If the pool admin allowlists the router to enable router-based swaps for legitimate users, every unpermissioned user can bypass the curated pool's allowlist by routing through the same public router contract, trading against LP assets that were deposited under the assumption of restricted access.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // whoever called pool.swap()
  recipient, ...
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` (L162-165).

`SwapAllowlistExtension.beforeSwap` then checks that value:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the address passed from the pool — the router's address when the router is the caller.

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly without encoding the original `msg.sender` into `extensionData`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // caller-supplied; extension does not decode it
  );
```

The extension has no logic to decode `extensionData` to recover the true initiator. The check resolves to `allowedSwapper[pool][router]` for all users routing through the router.

**Two concrete failure modes:**
1. **Router not allowlisted** — Legitimate allowlisted users cannot use the router; they must call the pool directly. Core swap functionality is broken for curated pools.
2. **Router allowlisted** (the only way to let allowlisted users use the router) — Every unpermissioned user bypasses the allowlist by routing through the public `MetricOmmSimpleRouter`, because `allowedSwapper[pool][router] = true` for all of them.

The same bypass applies to `exactInput` and `exactOutput` multi-hop paths, since every hop calls `pool.swap()` with `msg.sender = router`.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional desks, whitelisted protocols) loses that restriction entirely once the router is allowlisted. Any anonymous user can trade against the pool's liquidity at oracle-derived prices, extracting value from LPs who deposited under the assumption that only vetted counterparties could interact. This constitutes a direct loss of LP principal and a broken core pool invariant (the allowlist access control). This meets the "Broken core pool functionality causing loss of funds" and "Admin-boundary break" allowed impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a standard, publicly deployed periphery contract. Any user who discovers that a pool uses a swap allowlist can trivially route through the router with a single `exactInputSingle` call. No special privileges, flash loans, or complex setup are required. The pool admin has no way to prevent this without removing the router from the allowlist, which simultaneously breaks the router for legitimate users. The attack is repeatable and requires no preconditions beyond the router being allowlisted.

## Recommendation
The allowlist must gate the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: encode `msg.sender` into `extensionData` before calling `pool.swap()`, so extensions can recover the true initiator.
2. **In `SwapAllowlistExtension.beforeSwap`**: decode the initiator from `extensionData` when present and check that address against the allowlist, falling back to `sender` for direct pool calls.

The cleanest fix is to have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it when present, preserving backward compatibility for direct pool callers.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to let Alice use the router
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // Alice is a legitimate user
  - Bob is NOT allowlisted.

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(recipient=bob, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes. Bob receives tokens from the curated pool.

Expected: Bob's call reverts with NotAllowedToSwap.
Actual:   Bob's call succeeds because the router is allowlisted.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and `router`, then call `exactInputSingle` from `bob` and assert it does **not** revert — confirming the bypass. The same test applies to `exactInput` and `exactOutput` entry points.