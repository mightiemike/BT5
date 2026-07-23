### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass the Per-Pool Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` rather than the identity of the real user. A pool admin who allowlists the router address (the only way to enable router-mediated swaps on a curated pool) inadvertently opens the pool to every user, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the received `sender` against the per-pool allowlist, using `msg.sender` (the pool) as the mapping key: [3](#0-2) 

When a user calls the router, the router calls `pool.swap(...)` directly. The pool therefore sees `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]`. The real user's address is never consulted.

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps revert for everyone, including legitimately allowlisted users |
| Allowlist the router | Every user on the network can swap through the router, bypassing the per-user gate |

There is no configuration that simultaneously permits router-based swaps and enforces per-user allowlisting.

The `setAllowedToSwap` setter is correctly gated to the pool admin: [4](#0-3) 

But the check inside `beforeSwap` binds to the wrong actor once an intermediary is in the call path.

---

### Impact Explanation

Any user who is **not** on the swap allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`. The pool admin's access-control boundary is silently nullified for all router-mediated swaps. This is a direct admin-boundary break: an unprivileged path bypasses a configured pool guard, allowing unauthorized trading on a pool whose operator explicitly restricted swap access.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Any pool that uses `SwapAllowlistExtension` and also wants to support router-based swaps for its allowlisted users must allowlist the router address. This is the expected operational path, making the bypass reachable under normal deployment conditions. No privileged access, malicious setup, or non-standard token behavior is required — any EOA can call the router.

---

### Recommendation

The extension must resolve the real user's identity rather than accepting the intermediary's address. Two sound approaches:

1. **Pass the original caller through the router.** The router encodes the real `msg.sender` into `extensionData` (or a dedicated field), and the extension decodes and checks that address. The pool must verify the router is trusted before accepting a delegated identity claim.

2. **Check the real caller inside the extension.** The extension reads the original initiator from a trusted context (e.g., a transient-storage slot written by the router before calling the pool) and gates on that address instead of the `sender` argument.

Either approach must ensure the identity claim cannot be spoofed by an untrusted caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, bob, true)

Attack:
  1. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient, ...) → msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes
  5. Bob's swap executes on the curated pool despite not being allowlisted.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
