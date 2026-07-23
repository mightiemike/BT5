Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. This creates an irresolvable dilemma: allowlisting the router lets any unpermitted user bypass the restriction; not allowlisting it breaks router-mediated swaps for all permitted users.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`. In `MetricOmmPool.swap`, the pool calls:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← original caller of pool.swap()
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly with no encoding of the originating user:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // user-supplied, not auto-encoded with msg.sender
  );
```

The call chain is: `User → Router.exactInputSingle → pool.swap(msg.sender = Router) → extension.beforeSwap(sender = Router)`. The extension therefore checks `allowedSwapper[pool][Router]`, not `allowedSwapper[pool][User]`. No existing guard in the extension, pool, or router corrects this. The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the economic beneficiary passed explicitly), which is independent of the adder/router address.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional market makers, KYC'd users, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against LP liquidity that was deposited under the assumption of a curated, restricted trading environment. This constitutes a direct admin-boundary break with fund-impacting consequences for LPs: they bear swap risk from counterparties the pool was explicitly designed to exclude.

## Likelihood Explanation
The likelihood is high. Any pool admin who wants to allow permitted users to trade via the router must allowlist the router — this is the natural and expected configuration. Once the router is allowlisted, the bypass is trivially reachable by any public user with no special privileges, no preconditions, and no multi-step setup. The attacker only needs to call `router.exactInputSingle` targeting the restricted pool.

## Recommendation
The extension must gate the end user, not the immediate pool caller. Two viable approaches:

1. **Pass the end user through `extensionData`:** The router encodes `msg.sender` (the end user) into `extensionData`; the extension decodes and checks that address when `sender` is a recognized router.
2. **Trusted router registry:** Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData`.

The simplest correct fix is to have the router always encode the originating user in `extensionData` and have the extension decode and check that identity when `sender` is a recognized router address.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is permitted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  - charlie (not permitted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(msg.sender = router, ...)
  - Pool calls extension.beforeSwap(sender = router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - charlie's swap executes on the restricted pool

Result:
  - charlie trades against LP liquidity on a pool that was supposed to exclude him
  - The allowlist provides zero protection for router-mediated swaps
```