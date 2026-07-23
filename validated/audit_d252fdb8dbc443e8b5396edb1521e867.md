Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the end user. If the pool admin allowlists the router (required for any user to use the standard periphery path), every unprivileged user can bypass the per-user allowlist by routing through the router, constituting a complete access control bypass.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the value forwarded from the pool. In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is whoever called `pool.swap()`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to the extension: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)`, the pool's `msg.sender` is the router contract: [4](#0-3) 

So the extension receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]` — never checking the actual end user. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation
A pool admin deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses. To allow those users to use `MetricOmmSimpleRouter`, the admin must allowlist the router. Once `allowedSwapper[pool][router] == true`, the check passes for every caller of the router regardless of individual allowlist status. Any unprivileged user can execute swaps on the curated pool via a single router call. This is a complete bypass of the intended per-user access control — an admin-boundary break where an unprivileged path circumvents the pool admin's configured restriction, with direct fund-impact consequences (unauthorized parties trade against restricted LP positions).

## Likelihood Explanation
The bypass is active whenever the router is allowlisted for a pool using `SwapAllowlistExtension`. This is the expected operational configuration: without allowlisting the router, no user can use the standard periphery path on a curated pool. The pool admin is therefore incentivized to allowlist the router, unknowingly opening the bypass to all users. No special privileges, flash loans, or unusual token behavior are required — a single call to `exactInputSingle` suffices.

## Recommendation
Gate on the actual end user rather than the immediate `pool.swap()` caller. The simplest fix consistent with the existing design is to check `recipient` (the address that receives output tokens), since the router always sets this to the actual end user:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

A more robust alternative is to pass the originating user explicitly via `extensionData` (populated by the router with `msg.sender`) and have the extension decode and verify it, requiring coordinated changes to both the router and extension.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin allowlists `alice` directly: `setAllowedToSwap(pool, alice, true)`.
3. Admin allowlists the router so legitimate users can use it: `setAllowedToSwap(pool, address(router), true)`.
4. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(recipient=bob, ...)` — pool's `msg.sender` is the router.
6. `_beforeSwap(sender=router, recipient=bob, ...)` is invoked; the extension checks `allowedSwapper[pool][router] == true` → passes.
7. `bob` successfully swaps on the curated pool despite never being individually allowlisted.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
