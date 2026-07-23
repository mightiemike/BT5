### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. If the pool admin allowlists the router address (which is required for any router-mediated swap to succeed on an allowlisted pool), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the `_beforeSwap` hook is called with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` parameter of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

**Step 2 — SwapAllowlistExtension checks `sender` against the per-user allowlist.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`msg.sender` here is the pool (correct). `sender` is the direct caller of `pool.swap()`.

**Step 3 — MetricOmmSimpleRouter calls `pool.swap()` as itself.**

In `exactInputSingle`, the router calls `pool.swap()` directly. The pool sees `msg.sender = router`: [4](#0-3) 

The actual end user (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment settlement — it is never forwarded to the pool or the extension: [5](#0-4) 

**Step 4 — The broken invariant.**

The allowlist check resolves to `allowedSwapper[pool][routerAddress]`. For any router-mediated swap to succeed on an allowlisted pool, the admin must add the router to the allowlist. Once the router is allowlisted, the check passes for **every** user who routes through it, regardless of whether that individual user is in the allowlist. The per-user gate is completely bypassed.

The same structural flaw exists in `exactInput`, `exactOutputSingle`, and `exactOutput`: [6](#0-5) 

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd counterparties, institutional traders) is fully open to any unprivileged user once the router is allowlisted. Unauthorized swappers can execute trades against the pool's LP reserves at oracle-derived prices, extracting value from LPs through arbitrage or directional pressure that the allowlist was designed to prevent. This is a direct loss of LP principal attributable to a broken core guard.

---

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) intends to support the standard `MetricOmmSimpleRouter` path will face this issue. The admin must allowlist the router to enable router-mediated swaps for legitimate users; doing so silently opens the pool to all users. The trigger requires no privileged access — any EOA can call `exactInputSingle` on the router.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; `SwapAllowlistExtension` decodes and checks it when `sender` is a known router address.
2. **Check `sender` only when it is not a trusted router**: Maintain a registry of approved routers in the extension; when `sender` is a router, require the actual user identity to be present in `extensionData`.

The simplest safe default is to **not allowlist the router address** and require allowlisted users to call `pool.swap()` directly, but this breaks the intended UX and should be documented explicitly.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension.
// 2. Admin allowlists `allowedUser` and the router address.
// 3. `attacker` is NOT in the allowlist.

// Direct swap by attacker — correctly reverts:
vm.prank(attacker);
pool.swap(attacker, true, 1000, type(uint128).max, "", "");
// → reverts NotAllowedToSwap (allowedSwapper[pool][attacker] == false)

// Router swap by attacker — bypasses the allowlist:
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool:            address(pool),
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1000,
    amountOutMinimum: 0,
    priceLimitX64:   type(uint128).max,
    extensionData:   "",
    deadline:        block.timestamp + 1
}));
// → succeeds because allowedSwapper[pool][router] == true
// attacker receives token1 output; LP reserves are drained without authorization
```

The `sender` argument received by `beforeSwap` is `address(router)`, which is allowlisted, so the guard passes for every user who routes through it.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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
