Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user — allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted (required for any router-mediated swap to succeed), every unprivileged user can bypass the per-user allowlist by calling through the public router. If the router is not allowlisted, legitimately allowlisted users cannot use the router at all, breaking core swap functionality.

## Finding Description

**Call path:**

1. User (non-allowlisted EOA) calls `MetricOmmSimpleRouter.exactInputSingle()`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — at this point `msg.sender` inside the pool is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, passing the router as `sender`.
4. `ExtensionCalling._beforeSwap()` encodes and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. Inside `SwapAllowlistExtension.beforeSwap`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the **router**, so the check resolves to `allowedSwapper[pool][router]`. The actual EOA identity is never consulted.

**Root cause:** `MetricOmmPool.swap()` passes `msg.sender` (the direct caller) as `sender` to the extension. When the router intermediates, this is the router address, not the end user. The extension has no mechanism to recover the original user.

**Why existing guards fail:** The `onlyPool` modifier in `BaseMetricExtension` only verifies the extension is called by a registered pool — it does not validate that `sender` represents the true economic actor. There is no transient-storage or callback mechanism that threads the original EOA through to the extension.

**Exact wrong value:** `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][actual_EOA]`.

## Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institution-only) with `SwapAllowlistExtension` must allowlist the router for any router-mediated swap to succeed. Once the router is allowlisted, **any** unprivileged user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the public `MetricOmmSimpleRouter` and bypass the per-user allowlist entirely. This constitutes a complete allowlist bypass — a broken core pool functionality and direct policy failure on curated pools. Severity: **High**.

## Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it with no preconditions beyond holding the input token. The bypass is repeatable on every block and requires no privileged access, flash loan, or special timing. The only precondition is that the router is allowlisted, which is operationally necessary for the pool to be usable by any router-mediated flow.

## Recommendation

Pass the original user identity through the swap path. Two options:

1. **Preferred:** Store the originating user in transient storage inside the router before calling `pool.swap()`, and expose a `msgSender()` view on the router. Modify `SwapAllowlistExtension` to read the true caller from the router when `sender` is a known router address.
2. **Simpler:** Change `MetricOmmPool.swap()` to accept an explicit `swapper` parameter (defaulting to `msg.sender` for direct calls) and thread it through to extensions, with the router passing `msg.sender` (the EOA) as `swapper`.

Either way, the extension must gate on the address that is economically responsible for the swap, not the intermediary contract.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists only alice: allowedSwapper[pool][alice] = true
// 3. Pool admin also allowlists router (required for alice to use router):
//    allowedSwapper[pool][router] = true
// 4. Bob (non-allowlisted) calls:
MetricOmmSimpleRouter(router).exactInputSingle(
    ExactInputSingleParams({
        pool: curated_pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// 5. Pool calls _beforeSwap(sender=router, ...)
// 6. Extension checks allowedSwapper[pool][router] == true → passes
// 7. Bob's swap executes despite not being allowlisted
```

Foundry test: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist only `alice` and the router, then assert that `bob` calling through the router succeeds (demonstrating the bypass) while `bob` calling the pool directly reverts.