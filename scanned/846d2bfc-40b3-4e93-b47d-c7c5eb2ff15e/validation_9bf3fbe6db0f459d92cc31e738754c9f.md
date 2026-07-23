### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router (required for router-mediated swaps to function) inadvertently opens the gate to every user on-chain, because the extension can no longer distinguish an allowlisted user from a non-allowlisted one — both arrive at the pool with `sender = router`.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` keys its check on that `sender`.**

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`.

**Step 3 — Every router entry point calls `pool.swap()` directly, making the router the `sender`.**

`exactInputSingle`:
```solidity
IMetricOmmPoolActions(params.pool).swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

`exactInput` (all hops), `exactOutputSingle`, and `exactOutput` follow the same pattern — the router is always the direct caller of `pool.swap()`. [5](#0-4) 

**Step 4 — The structural conflict.**

For a pool with `SwapAllowlistExtension` to support router-mediated swaps at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every call that arrives through the router — regardless of who the actual user is. The extension has no visibility into the real initiator.

---

### Impact Explanation

Any non-allowlisted user can bypass the swap allowlist on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) instead of calling `pool.swap()` directly. The pool executes the swap and settles real token transfers. The allowlist gate — the only access-control mechanism on the swap path — is rendered completely ineffective for router-mediated flows. This breaks the core curated-pool invariant and constitutes a direct loss of policy enforcement with fund-impacting consequences (unauthorized swaps execute at oracle price against real LP capital).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented and deployed alongside the protocol. Any pool admin who configures `SwapAllowlistExtension` and also wants to support the standard router must allowlist it. This is a normal operational step, not an exotic configuration. The bypass is therefore reachable on any production curated pool that uses the router.

---

### Recommendation

The extension must gate the **economic actor**, not the **direct caller**. Two sound approaches:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives output tokens and is set by the user in all router calls (`params.recipient`). This is harder to spoof and represents the economically relevant party for output-side gating.

2. **Forward the real initiator via `extensionData`** — the router already accepts and forwards `extensionData` per-hop. Define a convention where the router encodes `msg.sender` into `extensionData`, and the extension verifies and decodes it (with the pool or factory as the trusted forwarder). This preserves full flexibility.

Either way, the extension must not rely on `sender` alone when the pool is reachable through a public intermediary.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is not allowlisted

Attack:
  1. alice calls pool.swap(...) directly
     → SwapAllowlistExtension checks allowedSwapper[pool][alice] == false → REVERT ✓

  2. alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: alice, ...})
     → router calls pool.swap(alice, ...)
     → pool calls _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
     → swap executes, alice receives output tokens ✗

Result: alice bypasses the allowlist and executes a swap on a curated pool she was explicitly excluded from.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
