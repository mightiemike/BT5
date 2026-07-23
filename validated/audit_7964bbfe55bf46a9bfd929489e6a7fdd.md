Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is sourced from `msg.sender` inside `MetricOmmPool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool's `swap()` caller is the router contract, so `sender` delivered to the extension is the router's address — not the actual user's address. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user on-chain, fully nullifying the allowlist.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  ...
```

`ExtensionCalling._beforeSwap()` encodes that value verbatim as the first argument of `IMetricOmmExtensions.beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)  // ← router address when routed
)
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool:

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

The same pattern holds for `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). In every router path the pool receives the router as `msg.sender`, so the extension evaluates `allowedSwapper[pool][router]` — never `allowedSwapper[pool][actual_EOA]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Consequence |
|---|---|
| Allowlist the router | Every on-chain address can swap; allowlist is nullified |
| Do not allowlist the router | Allowlisted users cannot use the router at all |

## Impact Explanation

**High — direct bypass of access control with fund-impacting consequences.** A curated pool using `SwapAllowlistExtension` is deployed specifically to restrict who can trade (e.g., institutional counterparties, KYC-verified addresses, or protocol-internal actors). If the pool admin allowlists the router (a natural step to support the standard periphery), any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool's oracle-priced liquidity. The pool's LP funds are exposed to the full public swap surface the admin intended to close. Unauthorized traders can extract value at oracle-fair prices from a pool whose LPs deposited under the assumption of a restricted counterparty set.

## Likelihood Explanation

**Medium.** The router is the canonical user-facing entry point shipped alongside the pool. A pool admin enabling a curated pool will naturally test direct-pool swaps (which the allowlist correctly blocks for non-listed addresses) and then attempt to allowlist the router to support the periphery — at which point the bypass is live. The mistake is easy to make because allowlisting the router is semantically equivalent to `setAllowAllSwappers(pool, true)`, but this equivalence is not surfaced anywhere in the code or interface.

## Recommendation

The extension must gate the economically relevant actor — the EOA initiating the trade — not the immediate caller of `pool.swap()`. The cleanest fix is to have the router encode `msg.sender` into `extensionData` before forwarding to the pool, and update `SwapAllowlistExtension.beforeSwap` to decode and check that field when present. This requires a coordinated change to the router and extension but preserves full allowlist semantics without relying on `tx.origin`.

## Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for approved users.
3. Pool admin does NOT call setAllowedToSwap(pool, eve, true)
   — Eve is an unauthorized address.

Attack
──────
4. Eve calls MetricOmmSimpleRouter.exactInputSingle({
       pool:      <curated pool>,
       recipient: eve,
       zeroForOne: true,
       amountIn:  X,
       ...
   });

5. Router calls pool.swap(eve, true, X, ..., extensionData).
   Inside pool.swap(): msg.sender == router.
   _beforeSwap(router, ...) is called.

6. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...).
   Extension evaluates:
     allowAllSwappers[pool]          → false
     allowedSwapper[pool][router]    → true   ← admin set this in step 2

7. Guard passes. Eve's swap executes at oracle price against LP funds.
   Eve was never individually allowlisted; the allowlist is bypassed.
```

Relevant code references:
- `MetricOmmPool.swap` passes `msg.sender` as `sender`: `metric-core/contracts/MetricOmmPool.sol` L230–231
- `ExtensionCalling._beforeSwap` forwards `sender` verbatim: `metric-core/contracts/ExtensionCalling.sol` L160–165
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol` L37
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly: `metric-periphery/contracts/MetricOmmSimpleRouter.sol` L72–80