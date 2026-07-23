### Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` of its own `swap()` call as `sender`. When `MetricOmmSimpleRouter` mediates the swap, `sender` becomes the router's address, not the actual user's address. A pool admin who allowlists the router to support router-mediated swaps inadvertently grants every user swap access, bypassing the curated allowlist entirely.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

In `MetricOmmPool.swap()`, the before-swap hook is invoked as:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards this value verbatim as the `sender` argument to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks that `sender` value.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the extension is called by the pool), so the check resolves to `allowedSwapper[pool][sender]` where `sender` is whoever called `pool.swap()`.

**Step 3 — The router is the direct caller of `pool.swap()`.**

`MetricOmmSimpleRouter` is the standard user-facing swap interface. It calls `pool.swap()` directly and implements `IMetricOmmSwapCallback` to settle payment. From the pool's perspective, `msg.sender = router`. Therefore `sender = router` is what the extension sees — not the actual end-user.

**Two resulting failure modes:**

| Scenario | Effect |
|---|---|
| Admin allowlists the router (to support router-mediated swaps) | Every user can bypass the allowlist by routing through the router |
| Admin does NOT allowlist the router | Allowlisted users cannot swap through the router at all — the standard periphery path is broken |

Neither outcome matches the pool admin's intent. The allowlist is designed to gate individual users, but it structurally cannot do so when the router is the direct pool caller.

---

### Impact Explanation

**Critical/High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that guarantee entirely for the router path. Any unprivileged user can execute swaps against the pool by routing through `MetricOmmSimpleRouter`, draining LP assets at oracle-quoted prices with no restriction. This is a direct loss of LP principal and a complete admin-boundary break on the allowlist policy.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary user-facing swap interface documented in the protocol. Any user who interacts with the protocol through the standard periphery triggers this path. No special setup, privileged access, or non-standard token behavior is required. The bug is structural and affects every pool that uses `SwapAllowlistExtension` with router-mediated swaps.

---

### Recommendation

The extension must gate the **actual end-user**, not the direct pool caller. Two viable approaches:

1. **Router forwards user identity in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling `pool.swap()`. The `SwapAllowlistExtension` decodes and checks that address. This requires a trusted encoding convention.

2. **Pool exposes a `swapFor(address user, ...)` entry point**: The pool accepts an explicit `user` parameter (validated against `msg.sender` or a trusted router list) and passes it as `sender` to extensions. This is the cleanest separation of payer from gated identity.

Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router, accepting that the router path is unavailable to all users.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured in beforeSwap order.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // to allow router-mediated swaps
4. bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
5. Router calls pool.swap(recipient=bob, ...) → msg.sender = router
6. _beforeSwap(sender=router, ...) is dispatched to SwapAllowlistExtension
7. Extension checks: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes at oracle price against LP funds — allowlist bypassed.
```

Conversely, if the admin does NOT allowlist the router:
```
4. alice (allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
5. Router calls pool.swap(...) → sender = router
6. Extension checks: allowedSwapper[pool][router] == false → NotAllowedToSwap()
7. Alice's swap reverts — standard periphery path is broken for all users.
``` [3](#0-2) [1](#0-0) [4](#0-3)

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
