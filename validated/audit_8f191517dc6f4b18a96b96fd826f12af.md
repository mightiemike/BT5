### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing swap allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on `sender`, which is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the actual user. A pool admin who adds the router to the allowlist to enable router-based swaps inadvertently grants every user the ability to bypass the per-address restriction.

---

### Finding Description

`SwapAllowlistExtension` is described as "Gates `swap` by swapper address, per pool." Its hook is:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (enforced by `onlyPool`). `sender` is the first argument forwarded by the pool's `_beforeSwap` internal call:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-177
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
```

`sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

So `sender` received by the extension equals the router address, not the actual user (`msg.sender` of the router call). The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

For a curated pool to support router-based swaps at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **every user** — regardless of whether they are individually allowlisted — can bypass the restriction by routing through `MetricOmmSimpleRouter`. The same issue applies to multi-hop `exactInput` and `exactOutput` paths, where the router calls `pool.swap` for each hop.

The `DepositAllowlistExtension` avoids this class of error by keying on `owner` (the position owner, the economic actor for deposits). The `SwapAllowlistExtension` should analogously key on the actual economic actor for swaps, but instead keys on the intermediary.

---

### Impact Explanation

Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter` once the router is added to the allowlist. The allowlist — the sole access-control mechanism for curated pools — is rendered ineffective for all router-based swap flows. Pools designed to restrict swap access to specific counterparties (e.g., institutional, KYC-gated, or regulatory-compliant pools) lose that protection entirely on the primary supported periphery path.

---

### Likelihood Explanation

Medium. The trigger is the pool admin adding the router to the allowlist, which is the natural and expected configuration for any curated pool that also wants to support the protocol's own router. There is no way to simultaneously allow router-based swaps and enforce per-user restrictions with the current extension design, so any pool admin who attempts to do so will unknowingly open the bypass.

---

### Recommendation

The extension must check the actual economic actor, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Check both `sender` and a user field:** Extend the `beforeSwap` signature or use `extensionData` to carry the originating user address, and gate on that field when `sender` is a known router.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin: setAllowedToSwap(pool, alice, true)   // Alice is the only allowed swapper
3. Pool admin: setAllowedToSw