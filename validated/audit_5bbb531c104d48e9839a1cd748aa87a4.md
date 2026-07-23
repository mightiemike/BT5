Based on my investigation of the extension system, I found a valid analog.

---

### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` validates the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (or `allowAllSwappers` is set), any non-allowlisted user can bypass the per-user swap allowlist by calling through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its allowlist check as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool from its own `_beforeSwap` internal call:

```solidity
function _beforeSwap(
    address sender,   // ← pool's msg.sender, i.e. the direct caller of pool.swap()
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap(recipient, ...)`. The pool's `msg.sender` at that point is the **router address**, so `sender` forwarded to the extension is the router, not the end user. The allowlist check becomes `allowedSwapper[pool][router]`. If the router is allowlisted (a natural production configuration), every user who routes through it bypasses the per-user allowlist entirely.

The allowlist mapping is keyed `allowedSwapper[pool][swapper]` and is set per-address by the pool admin:

```solidity
mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
mapping(address pool => bool) public allowAllSwappers;
``` [3](#0-2) 

### Impact Explanation

A pool operator deploys `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise approved addresses. Any non-approved user can call `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool. The pool receives `msg.sender = router`, the extension checks `allowedSwapper[pool][router]`, and if the router is allowlisted the swap proceeds. The allowlist guard is completely neutralized for all users of the router. This is a broken core pool functionality (admin-boundary break): the pool admin's access control is bypassed by an unprivileged path, and unauthorized parties can execute swaps that should be gated.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing entry point for swaps. In any production deployment where the router is allowlisted (to avoid blocking all router users), the bypass is trivially reachable by any address. No special privileges or setup are required beyond calling the public router.

### Recommendation

The extension must check the **actual end user**, not the direct pool caller. Two options:

1. **Pass the originating user through `extensionData`**: The router encodes the real user in `extensionData`; the extension decodes and validates it. This requires trust that the router correctly populates the field.

2. **Check `recipient` instead of `sender`** if the pool's design guarantees `recipient` is the beneficiary (weaker, context-dependent).

3. **Preferred — use `tx.origin` as a fallback or require direct pool calls**: Gate the allowlist on `tx.origin` when `sender` is a known router, or document that allowlisted pools must not be used with the router.

The cleanest fix is to have the pool pass the original initiating user as a dedicated field rather than reusing `msg.sender` as `sender`.

### Proof of Concept

1. Pool is created with `SwapAllowlistExtension` in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — a natural step to allow the router to serve the pool.
3. Non-allowlisted user `alice` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(alice, ...)`.
5. Pool calls `extension.beforeSwap(router, alice, ...)` — `sender = router`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Alice's swap executes despite not being individually allowlisted. [1](#0-0) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

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
