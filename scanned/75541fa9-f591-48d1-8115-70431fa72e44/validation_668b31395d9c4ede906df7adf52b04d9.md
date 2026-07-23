### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension checks whether the **router** is allowlisted rather than the **actual user**. If the router is allowlisted (the only way to let allowlisted users reach the pool through the router), any unprivileged user can bypass the swap allowlist entirely by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is whatever the pool forwarded as the swap initiator.

In `MetricOmmPool.swap()`, the pool forwards its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` argument delivered to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user's address:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

So when a user calls the router:

| Call chain | `sender` seen by extension |
|---|---|
| User → `pool.swap()` directly | User address ✓ |
| User → `router.exactInputSingle()` → `pool.swap()` | Router address ✗ |

The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This creates an irreconcilable conflict for any pool admin who configures `SwapAllowlistExtension`:

- **If the router is allowlisted** (the only way to let allowlisted users reach the pool through the router): every user — including those explicitly not allowlisted — can bypass the guard by calling the router.
- **If the router is not allowlisted**: allowlisted users cannot use the router at all, breaking the core swap flow for the supported periphery path.

There is no configuration that simultaneously enforces a per-user allowlist and permits use of the router.

---

### Impact Explanation

Curated pools using `SwapAllowlistExtension` are designed to restrict trading to specific addresses (e.g., KYC-verified users, institutional counterparties, or protocol-controlled addresses). Once the router is allowlisted — a necessary step for any allowlisted user who wants to use multi-hop or exact-output routing — the allowlist is completely defeated. Any unprivileged user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router and trade on the curated pool without restriction. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's intended access policy is bypassed by an unprivileged path through a supported public contract.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the official periphery swap contract. Pool admins who deploy a curated pool with `SwapAllowlistExtension` will naturally need to allowlist the router so that their permitted users can access multi-hop and exact-output flows. The moment they do so, the allowlist is open to all users. The trigger requires no special privilege, no malicious setup, and no non-standard token behavior — only a call to the public router.

---

### Recommendation

The `SwapAllowlistExtension` must check the economically relevant actor — the end user — not the intermediary router. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the originating `msg.sender` into `extensionData`; the extension decodes and checks that address. The pool admin must also configure the extension to trust the router as a forwarder.
2. **Separate router-aware allowlist**: Add a `trustedForwarder` mapping to the extension; when `sender` is a trusted forwarder, decode the real user from `extensionData` and check that address instead.

Until fixed, pool admins should be warned that `SwapAllowlistExtension` cannot enforce per-user access control when the router is allowlisted.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  extension.setAllowedToSwap(pool, alice, true)   // Alice is the only permitted swapper
  extension.setAllowedToSwap(pool, router, true)  // router allowlisted so Alice can use it

Attack:
  Bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

Execution:
  router → pool.swap()          msg.sender in pool = router
  pool   → _beforeSwap(router, ...)
  pool   → extension.beforeSwap(sender=router, ...)
  extension checks allowedSwapper[pool][router] → true
  swap executes for Bob with no revert
```

Bob successfully trades on a pool that was intended to be restricted to Alice, with no privileged access required.

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
