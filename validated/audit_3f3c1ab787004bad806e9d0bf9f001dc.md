Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct `msg.sender` of `MetricOmmPool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that value is the router's address, not the end-user's address. A pool admin who allowlists the router to enable router-based swaps inadvertently grants swap access to every caller of the router, bypassing the per-user allowlist entirely.

## Finding Description

**Root cause in `MetricOmmPool.swap()`.**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← sender = direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**Bypass path via `MetricOmmSimpleRouter`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The router is `msg.sender` when `pool.swap()` executes, so `sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants to enable router-based swaps must call `setAllowedToSwap(pool, router, true)`. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router — including addresses the admin never intended to permit. There is no field in the `beforeSwap` signature that carries the original end-user's address, and the router does not forward any user identity information to the extension.

**Existing guards are insufficient.** The `allowAllSwappers` flag is a separate, explicit open-door setting. The per-swapper mapping `allowedSwapper[pool][swapper]` is the only per-user gate, and it is keyed on the direct pool caller, not the originating user. No mechanism in the current call path allows the extension to distinguish between individual end-users when they route through the same intermediary contract.

## Impact Explanation
The swap allowlist is a core access-control mechanism for restricted pools. When the router is allowlisted (a necessary step for any admin who wants to support router-based swaps), the allowlist is rendered completely ineffective: any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and swap in a pool that was intended to be restricted to specific addresses. This is a broken core pool functionality — the allowlist invariant (`only approved addresses may swap`) is violated for all router-mediated swaps.

## Likelihood Explanation
The precondition is that the pool admin has allowlisted the router address. This is a natural and expected administrative action for any pool that intends to support the standard periphery router. Once that single admin action is taken, the bypass is immediately available to any unprivileged user with no further conditions. The attack is repeatable, requires no special privileges, and is not detectable on-chain before execution.

## Recommendation
The `beforeSwap` hook signature must carry the original end-user's identity. One approach is to have the router pass the originating user address in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. A cleaner fix is to add an `originator` field to the `beforeSwap` hook signature at the core level, populated by the pool as `tx.origin` or passed explicitly by the router. Alternatively, the extension can maintain a separate `trustedRouter` registry and, when `sender` is a trusted router, require the user address to be ABI-encoded in `extensionData` and verified there.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. An address `attacker` that has never been individually allowlisted calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(...)` with `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router] == true`, and passes.
6. The attacker's swap executes successfully despite never being individually allowlisted.

Foundry test sketch:
```solidity
// After admin allowlists the router:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (never individually allowlisted) swaps via router — should revert but does not:
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({pool: address(pool), ...}));
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```
