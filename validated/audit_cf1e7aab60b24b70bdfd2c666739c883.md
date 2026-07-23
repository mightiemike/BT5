Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates the router's allowlist status rather than the actual end-user's. A pool admin who allowlists the router to support standard tooling inadvertently grants every on-chain address unrestricted swap access, completely defeating the per-user curation the extension was deployed to enforce.

## Finding Description
**Root cause:** In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

So `sender` arriving at the extension is `address(router)`, not the user's EOA. The extension has no visibility into the original `msg.sender` of the router call.

**Two concrete failure modes:**

1. **Allowlist bypass:** Admin allowlists the router (`setAllowedToSwap(pool, router, true)`) to support standard tooling. Any non-allowlisted user calling `exactInputSingle`/`exactInput`/`exactOutputSingle`/`exactOutput` on the public router passes the check because `allowedSwapper[pool][router] == true`.

2. **Broken functionality for legitimate users:** If the admin does *not* allowlist the router, every individually-allowlisted user who swaps through the router is blocked with `NotAllowedToSwap`, because `sender = router` is not in the allowlist. The only working path is a direct `pool.swap()` call requiring the caller to implement `IMetricOmmSwapCallback` — an unreasonable burden for EOA users.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly ignores `sender` (the first, unnamed parameter) and gates on `owner`, which is passed explicitly by the liquidity adder as the position owner — avoiding this class of bug entirely:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-41
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

## Impact Explanation
**High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that guarantee entirely the moment the router is allowlisted. Any address on-chain can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`. This constitutes broken core pool functionality: the access control mechanism the pool admin explicitly configured is rendered ineffective, exposing LP assets to unrestricted swap flow. This falls squarely within the "Broken core pool functionality causing loss of funds or unusable swap flows" impact category.

## Likelihood Explanation
**High.** `MetricOmmSimpleRouter` is the canonical periphery entry point for swaps. A pool admin who configures `SwapAllowlistExtension` and also wants to support standard wallets, aggregators, or the protocol's own UI will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call available to any on-chain address.

## Recommendation
The extension must check the economically responsible actor, not the immediate pool caller. The recommended fix mirrors the `DepositAllowlistExtension` design: the router should forward the original caller's address as an authenticated field in `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and verify that field rather than the raw `sender` argument. At minimum, the extension documentation must warn that allowlisting the router grants unrestricted access to all users, and the admin interface should expose a "router-aware" mode that rejects router-mediated swaps unless user identity is separately authenticated.

## Proof of Concept
```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists alice and the router (to support standard tooling)
swapExt.setAllowedToSwap(pool, alice, true);
swapExt.setAllowedToSwap(pool, address(router), true);

// Bob is NOT allowlisted
// Direct call reverts correctly:
vm.prank(bob);
pool.swap(...);  // ← reverts NotAllowedToSwap (sender=bob, not allowlisted)

// But router-mediated call succeeds — bypass:
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: bob,
        ...
    })
);
// pool.swap() called with msg.sender=router
// extension checks allowedSwapper[pool][router] → true
// Bob's swap executes despite not being allowlisted
```