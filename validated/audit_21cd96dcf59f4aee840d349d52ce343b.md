Audit Report

## Title
`SwapAllowlistExtension` Checks Immediate Caller (`sender`) Instead of Originating User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate `msg.sender` of `MetricOmmPool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, the router becomes `msg.sender` to the pool, so the extension evaluates the router's address — not the originating EOA. A pool admin who allowlists the router to support router-mediated swaps for curated users inadvertently grants every non-allowlisted user the ability to bypass the restriction by routing through the router.

## Finding Description

`MetricOmmPool.swap()` captures `msg.sender` and passes it verbatim as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- immediate caller, not originating user
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` encodes that `sender` and dispatches it to every configured extension without modification (L149-177). `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` is used, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

At that point `msg.sender` inside `MetricOmmPool.swap()` is the router contract, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originating_EOA]`.

`DepositAllowlistExtension` avoids this exact problem because it checks `owner` — the position beneficiary explicitly passed as a separate parameter — rather than `sender`:

```solidity
// DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

`owner` is always the actual beneficiary regardless of who calls `addLiquidity`. No equivalent originator field exists in the swap path.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user executes a real swap against pool liquidity and receives output tokens. The pool's curation policy is silently voided — tokens flow to actors the pool was explicitly configured to exclude. This is broken core pool functionality (allowlist-gated swap policy) with direct fund-flow consequences.

## Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a natural and expected configuration step for any curated pool that also wants to support the standard periphery UX. The admin has no mechanism to simultaneously allow router-mediated swaps for allowlisted users and block them for non-allowlisted users, because the extension cannot distinguish the two cases. Any non-allowlisted user who discovers the router is allowlisted can exploit this immediately with a standard router call. No special privileges or unusual conditions are required beyond the router being allowlisted.

## Recommendation

Pass the originating caller's identity through the router to the pool, and have the extension check that identity. One approach: the router encodes its own `msg.sender` into `extensionData`; the extension decodes and checks it when `sender` is a known/trusted router. Alternatively, the extension can maintain a separate registry of trusted routers and, when `sender` is a trusted router, require that the originator (decoded from `extensionData`) is also allowlisted — enforcing both legs, analogous to how `DepositAllowlistExtension` checks `owner` rather than `sender`.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (non-allowlisted EOA) calls MetricOmmSimpleRouter.exactInputSingle(...)
     targeting the curated pool.
  2. Router calls pool.swap(recipient=attacker, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
  5. Swap executes; attacker receives output tokens.

Result:
  attacker successfully swaps on a pool that was supposed to block them,
  because the allowlist check evaluated the router address (allowlisted)
  rather than the attacker's address (not allowlisted).
```