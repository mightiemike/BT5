### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()` (i.e., `msg.sender` inside the pool). When users route through `MetricOmmSimpleRouter`, `sender` resolves to the router address, not the actual end user. If the pool admin allowlists the router to enable router-based swaps for curated users, every unprivileged user can bypass the restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension caller). `sender` is the value the pool passes as the first argument to `_beforeSwap`, which is always `msg.sender` of the pool's own `swap()` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

So `msg.sender` inside the pool = the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants to allow curated users to swap through the router must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every caller of the router — including users who were never individually allowlisted. The extension has no mechanism to recover the actual end user's identity from the router call.

The same path applies to `exactInput` (multi-hop) and `exactOutput` / `exactOutputSingle`, all of which call `pool.swap()` from the router's address.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position owner passed explicitly to `addLiquidity`), which is correctly threaded through `MetricOmmPoolLiquidityAdder` as the caller-supplied `owner` argument.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a specific set of counterparties (e.g., institutional traders, KYC'd addresses) is rendered ineffective the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute a swap that the allowlist was designed to block. Consequences include:

- **Unauthorized swap execution**: Non-allowlisted users trade against LP capital that was reserved for specific counterparties, directly extracting value from LP positions at oracle-anchored prices.
- **Broken curation invariant**: The pool's business logic guarantee — "only allowlisted addresses may swap" — is violated on every router-mediated call, matching the "Broken core pool functionality causing loss of funds" impact gate.
- **Admin-boundary bypass by unprivileged path**: The pool admin's allowlist policy is circumvented by any user who routes through the supported periphery entry point, satisfying the "factory/oracle role checks are bypassed by an unprivileged path" criterion.

---

### Likelihood Explanation

The prerequisite is that the pool admin allowlists the router address in `SwapAllowlistExtension`. This is a natural and expected operational step: without it, no user can swap through the