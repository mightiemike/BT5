Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `msg.sender` inside the pool — and therefore the `sender` the extension receives — is the router address, not the end user. Any pool admin who allowlists the router (required for any vetted user to trade through it) simultaneously opens the gate to every unprivileged caller of the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every extension in `BEFORE_SWAP_ORDER`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

When any user calls the router, `msg.sender` inside `pool.swap()` is the router address. The extension therefore evaluates `allowedSwapper[pool][router]`. For any allowlisted user to trade through the router, the pool admin must set `allowedSwapper[pool][router] = true`. Once that entry exists, every caller of the router — including completely non-allowlisted addresses — passes the `beforeSwap` check, because the extension cannot distinguish between different users behind the same router address. The router contains no per-pool allowlist check of its own.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position recipient, a user-supplied field), not `sender`, which avoids this problem for the liquidity path:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

No equivalent user-identifying field exists on the swap path that the extension could use instead of `sender`.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute directional swaps against LP positions, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade against them. This constitutes direct loss of LP principal attributable to unauthorized swap execution, matching the "admin-boundary break bypassed by an unprivileged path" and "broken core pool functionality causing loss of funds" criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entrypoint for swaps and is expected to be used by the vast majority of traders. Any pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist, which simultaneously opens the gate to all users. No special setup, privileged access, timing, oracle manipulation, or state precondition is required by the attacker — any EOA or contract can call the router. The bypass is deterministic and repeatable.

## Recommendation
The `SwapAllowlistExtension` must gate on the actual end user, not the direct caller of `pool.swap()`. The cleanest fix is to enforce the allowlist at the router level: add a per-pool allowlist check inside the router's swap path so that the router itself rejects non-allowlisted callers before calling `pool.swap()`. Alternatively, require the router to encode the originating user address in `extensionData` and have the extension decode and verify it, though this introduces a trust assumption on the router. Checking `recipient` instead of `sender` is insufficient because `recipient` is caller-supplied and may not equal the end user.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  allowedSwapper[pool][allowedUser]  = true
  allowedSwapper[pool][router]       = true  ← required for allowedUser to use router
  allowedSwapper[pool][attacker]     = false

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(recipient=attacker, ...)
    → pool passes msg.sender=router as `sender` to _beforeSwap
    → SwapAllowlistExtension.beforeSwap receives sender=router
    → allowedSwapper[pool][router] == true → check passes
    → swap executes, attacker receives output tokens
    → LP funds reduced by the swap delta

Result:
  attacker successfully swaps on a pool they are not allowlisted for,
  bypassing the curated-pool protection entirely.
```