Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any user to bypass per-user swap curation when the router is allowlisted - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable allowlisted users to use it simultaneously opens the pool to every user, completely defeating the per-user curation invariant the extension is designed to enforce.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension caller) and `sender` is the first argument the pool passes through `ExtensionCalling._beforeSwap`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

`ExtensionCalling._beforeSwap` passes `sender` verbatim from the pool's `swap()` entry point, which sets it to `msg.sender` of the `pool.swap()` call:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-176
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...)));
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The existing test confirms this: it allowlists `address(callers[0])` (the `TestCaller` contract that directly calls `pool.swap()`), not `users[0]` (the EOA initiating the transaction):

```solidity
// metric-periphery/test/extensions/FullMetricExtension.t.sol L70
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
```

The dilemma is irresolvable under the current design: if the admin allowlists only specific EOAs, those users cannot swap through the router (router is not allowlisted → revert). If the admin allowlists the router so that allowlisted users can use it, every user can swap through the router because the extension sees the allowlisted router address, not the caller. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

`DepositAllowlistExtension` does not share this problem because it gates on `owner` (the position beneficiary explicitly passed by the caller), not on `sender` (the direct pool caller).

## Impact Explanation
Medium. A non-allowlisted user can execute swaps on a pool whose admin intended to restrict trading to a curated set of addresses. The bypass is unconditional once the router is allowlisted: any address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension passes because it sees the allowlisted router rather than the non-allowlisted caller. This breaks the core curation invariant of `SwapAllowlistExtension` and allows unauthorized parties to trade against pool liquidity, constituting broken core pool functionality per the allowed impact gate.

## Likelihood Explanation
Medium. The bypass requires the pool admin to have allowlisted the router address. This is the natural and expected production configuration for any curated pool that also wants to support the standard periphery router UX. The scenario is not exotic; it is the normal setup. Any non-allowlisted user can then exploit it unconditionally and repeatably.

## Recommendation
The extension must verify the originating user, not the direct `pool.swap()` caller. Two viable approaches:

1. **Pass the originator through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. The router is a protocol-controlled contract and can be trusted to encode the correct originator.
2. **Add an explicit `originator` field to the swap hook interface:** The pool passes both `sender` (direct caller) and an `originator` set by the caller. The extension gates on `originator`, mirroring how `beforeAddLiquidity` gates on `owner` rather than `sender`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, allowedUser, true)` — only `allowedUser` is intended to swap.
3. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — necessary for `allowedUser` to use the router.
4. `nonAllowedUser` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(sender=router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `nonAllowedUser` has successfully swapped on a pool they were never meant to access.