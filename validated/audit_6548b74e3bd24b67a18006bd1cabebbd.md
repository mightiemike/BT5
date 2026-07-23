Audit Report

## Title
SwapAllowlistExtension Gates the Router's Identity Instead of the Original Swapper, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently grants every address on the network the ability to swap against the restricted pool, because any caller can route through the same router address.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension hook (L149-177). `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

So the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Once the pool admin adds the router to `allowedSwapper` (a natural operational step to enable router-mediated swaps for approved users), the check passes for every caller regardless of who the actual end user is. The same structural problem applies to multi-hop `exactInput`, where intermediate hops use `address(this)` (the router itself) as the effective caller.

The `DepositAllowlistExtension` does not share this flaw — it gates by `owner` (the position owner), which is an explicit argument set by the caller and preserved correctly through the `MetricOmmPoolLiquidityAdder` path.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to KYC-approved counterparties or any curated set loses that restriction entirely the moment the router is allowlisted. Any unprivileged address can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`. LP positions are directly exposed to uninvited order flow — including adversarial flow the allowlist was designed to exclude — resulting in direct loss of LP assets at oracle-quoted prices without the pool admin's consent. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where an unprivileged path bypasses an access control the pool admin explicitly configured.

## Likelihood Explanation
The pool admin must allowlist the router for this to be exploitable. This is a natural and expected operational step: without it, every allowlisted user is forced to call `pool.swap()` directly and loses access to multi-hop routing, ETH wrapping, permit flows, and slippage protection that the router provides. The extension interface and its documentation contain no warning that allowlisting the router collapses the allowlist to "allow all." A pool admin following the obvious deployment path will trigger the vulnerability. The attack is repeatable by any address with no special privileges.

## Recommendation
The extension must gate on the original end-user identity, not the immediate `pool.swap()` caller. Two sound approaches:

1. **Trusted-forwarder pattern**: The router encodes the original `msg.sender` in `extensionData`; the extension verifies the router's identity before trusting the forwarded address and checks `allowedSwapper[pool][decodedUser]`.
2. **Recipient-based gating**: Gate on `recipient` rather than `sender`. The recipient is the address that receives output tokens and is set by the end user, not the router. This is already available as the second argument to `beforeSwap`.

The simplest safe fix is to add a `trustedForwarder` mapping to the extension and, when `sender` is a trusted forwarder, decode and verify the real user from `extensionData`.

## Proof of Concept
```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension attached to BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC-approved
3. Pool admin calls setAllowedToSwap(pool, router, true)  // enable router for alice

Attack
──────
4. Bob (not KYC-approved, not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      pool,
           recipient: bob,
           zeroForOne: true,
           amountIn:  X,
           ...
       })

5. Router calls pool.swap(bob, true, X, ...) → msg.sender to pool = router
6. Pool calls _beforeSwap(router, bob, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true → passes
8. Swap executes; Bob receives output tokens from the restricted pool.

Result: Bob bypassed the allowlist with zero privileged access.

Foundry test outline:
- Deploy pool with SwapAllowlistExtension
- setAllowedToSwap(pool, alice, true)
- setAllowedToSwap(pool, router, true)
- vm.prank(bob); router.exactInputSingle(...)
- Assert swap succeeded despite bob not being in allowedSwapper
```