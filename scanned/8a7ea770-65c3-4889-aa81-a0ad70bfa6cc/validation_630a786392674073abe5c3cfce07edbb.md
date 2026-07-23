The call chain is fully traceable. Let me confirm the exact `sender` binding at each hop.

The call chain is fully confirmed. Here is the complete trace and verdict:

---

### Title
Swap Allowlist Bypass via Router Intermediary: `sender` Bound to Router Address Instead of Originating User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the value passed by the pool — which is `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract address**, not the originating EOA. Any pool admin who allowlists the router address (a natural action to enable router-mediated swaps for their users) inadvertently grants every unprivileged user the ability to bypass the allowlist.

---

### Finding Description

**Step 1 — Router calls pool directly:**

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` as `msg.sender`: [1](#0-0) 

**Step 2 — Pool binds `msg.sender` (= router) as `sender` for the hook:**

`MetricOmmPool.swap` passes `msg.sender` directly to `_beforeSwap`: [2](#0-1) 

**Step 3 — Extension receives router address as `sender`:**

`ExtensionCalling._beforeSwap` encodes `sender` (= router) into the hook call: [3](#0-2) 

**Step 4 — Allowlist check is against the router, not the user:**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router: [4](#0-3) 

If `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, regardless of whether the originating EOA is on the allowlist.

---

### Impact Explanation

The `SwapAllowlistExtension` is documented as gating `swap` by swapper address, per pool. [5](#0-4) 

When the router is allowlisted, the extension's entire access-control invariant collapses: any EOA can call `router.exactInputSingle()` and the pool will execute the swap as if the caller were authorized. This is a broken core pool functionality impact — the allowlist extension's sole purpose is defeated by a public periphery path.

---

### Likelihood Explanation

A pool admin who wants their allowlisted users to be able to trade via the standard router will naturally call `setAllowedToSwap(pool, router, true)`. There is no documentation or on-chain guard warning that doing so opens the allowlist to all users. The bypass requires no special privileges, no malicious setup, and no non-standard token behavior — only a call to the public `exactInputSingle` function. [6](#0-5) 

---

### Recommendation

The extension must identify the originating user, not the immediate caller. Two options:

1. **Pass `tx.origin` as an additional field** in the hook data (with the caveat that `tx.origin` is the EOA for all non-contract callers, which is the intended gate).
2. **Require the router to forward the originating user** via `extensionData`, and have `SwapAllowlistExtension` decode and verify that address. The pool admin would then allowlist individual users, not the router.

The cleanest production fix is option 2: define a convention where the router encodes `msg.sender` into `extensionData`, and the extension decodes and checks that value instead of the raw `sender` argument.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Deploy pool with SwapAllowlistExtension.
// Pool admin allowlists ONLY the router address (natural action to enable router swaps).
// Non-allowlisted EOA calls exactInputSingle — swap succeeds when it should revert.

swapExtension.setAllowedToSwap(address(pool), address(router), true);
// alice is NOT individually allowlisted

vm.prank(alice); // non-allowlisted EOA
uint256 out = router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        tokenIn:          address(token0),
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1_000,
        amountOutMinimum: 0,
        recipient:        alice,
        deadline:         type(uint256).max,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// Expected: revert NotAllowedToSwap
// Actual:   swap succeeds — allowedSwapper[pool][router] == true
assert(out > 0);
```

The pool's `_beforeSwap` receives `sender = address(router)`, the extension checks `allowedSwapper[pool][router] = true`, and the swap executes for the non-allowlisted `alice`.

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-10)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
