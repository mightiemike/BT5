Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of original user — allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, and therefore to `SwapAllowlistExtension.beforeSwap`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original EOA. The extension therefore checks the router's address against the allowlist instead of the actual trader's address. Any user can bypass a pool's swap allowlist by routing through the public router.

## Finding Description

**Root cause — `sender` binding in `MetricOmmPool.swap`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`), the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

From the pool's perspective, `msg.sender` is the **router address**. The pool passes this to `_beforeSwap`, which forwards it as `sender` to `SwapAllowlistExtension.beforeSwap`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router address (wrong — should be the original EOA). The check `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`.

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific EOAs (e.g., KYC'd users).
2. To support router-mediated swaps for those users, the admin must also allowlist the router address (`allowedSwapper[pool][router] = true`).
3. Once the router is allowlisted, **any** unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will see `sender = router`, pass the check, and execute the swap — completely bypassing the per-user allowlist.
4. Alternatively, if the admin does not allowlist the router, legitimately allowlisted users cannot swap through the router at all, breaking the supported periphery path.

**Existing guards are insufficient:** The `onlyPool` modifier on `BaseMetricExtension` only verifies the extension is called by a registered pool — it does not recover the original EOA. No mechanism threads the original `msg.sender` through the router to the extension.

## Impact Explanation
A curated pool's swap allowlist (e.g., KYC gate, whitelist-only trading) is completely bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. This constitutes broken core pool functionality and a direct admin-boundary break: the pool admin's intended access control is rendered ineffective. Any user can trade on a pool that was designed to restrict trading to specific addresses.

## Likelihood Explanation
The `MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call it without preconditions. The bypass is deterministic and repeatable on every swap. No special privileges, flash loans, or timing are required. The only precondition is that the pool admin has configured `SwapAllowlistExtension` and allowlisted the router (a necessary step to support router-mediated swaps for legitimate users).

## Recommendation
Pass the original caller's identity through the router to the pool, and have the pool forward it to extensions. One approach: encode the original `msg.sender` in `callbackData` or `extensionData` inside the router, and have the pool extract and pass it as `sender` to hooks. Alternatively, `SwapAllowlistExtension.beforeSwap` should check the original EOA by reading it from a trusted source (e.g., a transient-storage slot set by the router before calling the pool, readable by the extension via a router interface). The simplest correct fix is to have the router store `msg.sender` in transient storage before calling `pool.swap`, and have the extension read it from the router when `sender` is the router address.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, only alice is allowlisted
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    // Admin also allowlists the router so alice can use it
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Bob (not allowlisted) calls the router directly
    vm.startPrank(bob);
    token0.approve(address(router), type(uint256).max);
    // This succeeds — extension sees sender=router (allowlisted), not bob
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: bob,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // Bob successfully swapped despite not being on the allowlist
}
```