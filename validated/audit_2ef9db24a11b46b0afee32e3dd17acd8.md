Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, enabling full allowlist bypass — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument forwarded by the pool, which is `msg.sender` of the pool's `swap()` call — the direct caller. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the router, not the user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the gate to every user, completely defeating the curation policy.

## Finding Description
The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of swap(), i.e. the router
    recipient,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol L149-176
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` value:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is called, it calls `pool.swap(recipient, ...)` directly — making the router the `msg.sender` of the pool's `swap()` call:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The extension therefore evaluates `allowedSwapper[pool][router]`, never seeing the originating user's address. This creates a binary failure: if the router is not allowlisted, all allowlisted users are locked out of the primary swap interface; if the router is allowlisted, every user — including explicitly excluded ones — can bypass the allowlist with a single router call.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the LP position owner, not the adder contract), demonstrating the correct pattern is understood and applied on the liquidity side but not the swap side.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelist-only launch participants) loses its curation guarantee the moment any non-allowlisted user routes through `MetricOmmSimpleRouter`. LP funds in the pool are exposed to trades from actors the pool admin explicitly excluded. This is broken core pool functionality causing direct exposure of LP assets to unauthorized actors — High severity.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any non-allowlisted user rejected by a direct pool swap will naturally try the router next. The bypass requires no privileged access, no special token, and no multi-step setup — a single `exactInputSingle` or `exactInput` call through the router suffices. Pool admins who allowlist the router to restore usability for legitimate users will unknowingly open the gate to all users. Likelihood is High.

## Recommendation
The extension must check the address of the economic actor, not the intermediary. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This is acceptable given the router is a protocol-controlled contract.
2. **Check `recipient` instead of `sender` for router flows**: Since `recipient` is the address receiving output tokens, it more closely represents the economic actor in single-hop swaps, though it is not a perfect substitute in all multi-hop configurations.

The `DepositAllowlistExtension` pattern of checking `owner` rather than `sender` should be mirrored on the swap side.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is meant to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` so `userA` can use the router (otherwise `userA`'s router swaps revert with `NotAllowedToSwap`).
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Router calls `pool.swap(recipient=userB, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. `userB`'s swap executes successfully on the supposedly curated pool.

The only way to prevent step 8 is to remove the router from the allowlist, which simultaneously breaks `userA`'s ability to use the router at step 3.