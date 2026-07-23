### Title
`SwapAllowlistExtension` gates the router address instead of the ultimate user, allowing any unprivileged caller to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the user's address. A pool admin who allowlists the router to support router-based swaps for their approved users inadvertently opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the `beforeSwap` call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether `sender` (the first argument) is in the per-pool allowlist, using `msg.sender` (the pool) as the mapping key: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`, so `msg.sender` inside the pool is the router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an impossible choice for the pool admin:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all |
| Router **allowlisted** | Every user on the network can bypass the allowlist via the router |

A pool admin who wants their approved users to be able to use the standard periphery router will allowlist the router, unknowingly granting unrestricted swap access to the entire network.

---

### Impact Explanation

Any user can execute swaps on a pool that is intended to be restricted to a specific set of approved addresses (e.g., KYC-gated, institutional-only, or compliance-restricted pools). The allowlist extension's core invariant — that only approved addresses may swap — is silently broken for all router-mediated swaps. Depending on the pool's purpose, this can result in unauthorized trading, circumvention of regulatory controls, or extraction of LP value by actors the pool admin explicitly excluded.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented entry point for swaps in the periphery layer. Pool admins who configure a `SwapAllowlistExtension` and also want their approved users to have a normal UX will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no malicious contracts — any EOA can call the router.

---

### Recommendation

The extension must verify the **ultimate user**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router.** The router should accept a `swapper` parameter and pass it as `extensionData`; the extension reads the declared swapper from `extensionData` and verifies it against `msg.sender` of the router call (requires a trusted-router pattern or a signed payload).

2. **Check `tx.origin` as a fallback** (acceptable only if the threat model excludes contract callers) — but this is generally discouraged.

3. **Preferred:** Redesign the allowlist to gate at the router level. The router checks the allowlist before calling the pool, and the extension's `onlyPool` guard ensures only the trusted router can reach the pool. This separates concerns cleanly.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is approved
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (by bob, who is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInput(pool, ...)
  2. Router calls pool.swap(...) — msg.sender inside pool = router
  3. Pool calls SwapAllowlistExtension.beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true
  5. Swap executes successfully for bob despite bob not being in the allowlist

Result: bob bypasses the allowlist entirely.
```

The `SwapAllowlistExtension` allowlist check on `sender`: [4](#0-3) 

The pool's `swap()` passing `msg.sender` (the router) as `sender`: [5](#0-4)

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
