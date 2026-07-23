Based on my investigation of the codebase, I can trace the following analog:

---

### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any Unprivileged Swapper to Bypass the Configured Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so `sender` passed to the extension is the router's address. If the router is allowlisted (which is required for any router-mediated swap to succeed on an allowlisted pool), every user who routes through the router bypasses the per-user allowlist gate entirely.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the address the pool forwards as the swapper identity. The pool's `_beforeSwap` dispatcher in `ExtensionCalling.sol` passes its own `sender` parameter directly:

```solidity
function _beforeSwap(
    address sender,
    ...
) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)
      )
    );
}
``` [2](#0-1) 

The pool sets `sender = msg.sender` of the `swap` call. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so `sender` arriving at the extension is the router's address — not the end-user's address.

The pool admin who wants to allow router-mediated swaps must allowlist the router address (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any** user — including those the admin explicitly never allowlisted — can call `MetricOmmSimpleRouter` and have their swap pass the `beforeSwap` guard, because the extension only sees the router address.

This is the direct analog to the SSL bug: the security control (allowlist) is configured and present, but it is applied to the wrong identity (the intermediary router rather than the actual end-user), rendering it ineffective for the threat it was designed to stop.

### Impact Explanation

Any user who is not on the allowlist can bypass the swap gate on a restricted pool by routing through `MetricOmmSimpleRouter`. The pool admin's intent — to restrict swapping to a curated set of addresses — is completely defeated. Non-allowlisted users can execute swaps, draining LP value or executing arbitrage on pools that were supposed to be closed to them. This is a broken core pool access-control invariant with direct fund-impacting consequences for LPs.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard public entry point for swaps. Any pool that uses the allowlist extension and also permits router-mediated swaps (the common case) must allowlist the router, triggering the bypass. The attacker needs no special privileges — only knowledge of the router address and the pool.

### Recommendation

The pool should pass the **original end-user** as `sender` rather than `msg.sender`. One approach: require the caller to supply the true originator explicitly and validate it (similar to how Uniswap v4 passes `msgSender` through the unlock callback). Alternatively, `SwapAllowlistExtension.beforeSwap` should check `recipient` (the address that receives tokens) or require the router to forward the real user identity in `extensionData`, and the extension should decode and gate on that value.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** allowlist `attacker`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. The pool calls `_beforeSwap(msg.sender=router, ...)` → extension receives `sender=router`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `attacker` successfully swaps on a pool they were never authorized to access. [3](#0-2) [2](#0-1)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
