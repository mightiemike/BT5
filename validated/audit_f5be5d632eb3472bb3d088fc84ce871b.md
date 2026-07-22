### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass Curated Pool Swap Restrictions via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the **router address**, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the network can bypass the individual allowlist by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` then encodes that value and forwards it verbatim to every configured extension: [2](#0-1) 

**Step 2 — SwapAllowlistExtension checks `sender` as the gated identity.**

`SwapAllowlistExtension.beforeSwap` uses `sender` (the first parameter, which is `msg.sender` of the pool call) as the identity to look up in the per-pool allowlist: [3](#0-2) 

**Step 3 — When the router calls the pool, `sender` = router address.**

`MetricOmmSimpleRouter` calls `pool.swap(...)` directly. At the pool level, `msg.sender` is the router contract, so `sender` forwarded to the extension is the router address — not the end user who initiated the transaction.

**Step 4 — The mismatch creates a binary dilemma for the pool admin.**

| Admin choice | Effect |
|---|---|
| Allowlist the router | Every user on the network can bypass the individual allowlist by routing through the public router |
| Do NOT allowlist the router | Individually allowlisted users cannot use the router at all; they must call `pool.swap()` directly |

There is no configuration that simultaneously enforces per-user allowlisting **and** permits router-mediated swaps. The allowlist is structurally bypassed for any pool that needs to support the standard periphery path.

**Analog to the seed bug.** The VaultRegistry bug removed the wrong vault because the function accepted two independent identifiers (address + ID) without verifying they referred to the same entity. Here, the extension accepts two independent actor identifiers (`sender` = router, actual user = transaction origin) without verifying they refer to the same gated party. In both cases, the guard operates on the wrong value and the intended protection fails silently.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties is completely defeated. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInput()` or `exactOutput()` targeting the curated pool. The router's address passes the allowlist check, the swap executes, and the user receives pool output tokens. This constitutes a direct policy bypass with fund-impacting consequences: disallowed users trade against LP capital that was deposited under the assumption that only approved counterparties could access the pool.

---

### Likelihood Explanation

Likelihood is high. The attack requires no special permissions, no flash loans, and no privileged access. Any user who can call the public router can exploit this. The router is a standard, documented periphery contract. The only precondition is that the pool admin has allowlisted the router (which is the natural operational choice to support normal trading UX). The bypass is deterministic and repeatable on every block.

---

### Recommendation

The `sender` forwarded to extensions must represent the economically relevant actor, not the intermediary contract. Two concrete fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the original `msg.sender` (the end user) into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when `extensionData` is non-empty.

2. **Extension-side**: `SwapAllowlistExtension` should expose a secondary allowlist keyed on `recipient` (the second parameter of `beforeSwap`) and check it when `sender` is a known router, or require that `sender == recipient` for direct-user enforcement.

The core invariant to enforce: **the identity checked by the allowlist must be the same actor to whom the economic output of the swap is attributed.**

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is the only allowed swapper.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router must be allowlisted for UX.
4. Bob (not allowlisted) calls router.exactInput({pools: [pool], ...}).
5. Router calls pool.swap(recipient=bob, ...) — msg.sender at pool = router.
6. _beforeSwap forwards sender=router to SwapAllowlistExtension.
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes against LP capital; the allowlist is bypassed.

Direct call by Bob (pool.swap() directly) would correctly revert:
  allowedSwapper[pool][bob] == false → NotAllowedToSwap.
``` [4](#0-3) [5](#0-4)

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
