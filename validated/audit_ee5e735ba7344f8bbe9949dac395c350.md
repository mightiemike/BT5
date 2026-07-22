### Title
SwapAllowlistExtension Checks Router Address as Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the **router contract address**, not the originating user. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed), every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Actor binding in the pool's `swap` function**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

**What the allowlist extension actually checks**

`SwapAllowlistExtension.beforeSwap` receives `sender` and checks it against the per-pool allowlist: [3](#0-2) 

**What the router passes as `msg.sender` to the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool therefore sees `msg.sender == address(router)`: [4](#0-3) 

The same applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`.

**The broken invariant**

The pool admin intends to allowlist individual users (e.g., KYC'd addresses). The admin calls `setAllowedToSwap(pool, userA, true)`. For router-mediated swaps to work at all, the admin must also call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any** address — including non-allowlisted users — can call `router.exactInputSingle(...)` and the extension will see `sender == router`, pass the check, and execute the swap. The per-user allowlist is completely bypassed.

Contrast this with `DepositAllowlistExtension`, which correctly checks the `owner` argument (the economic actor) rather than `sender` (the caller of `addLiquidity`): [5](#0-4) 

The deposit extension is safe because `owner` is the position holder regardless of who calls `addLiquidity`. The swap extension is broken because `sender` changes depending on whether the user calls the pool directly or through the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC, institutional, or whitelist-only pools) provides **zero protection** once the router is allowlisted. Any unpermissioned user routes through `MetricOmmSimpleRouter` and trades freely. This is a direct loss of the curation policy the pool admin paid to enforce, and it exposes LP funds to trades from actors the pool was explicitly designed to exclude. Severity: **High** — broken core pool access control with direct fund-impact consequence (LP exposure to unintended counterparties, potential regulatory/compliance breach for curated pools).

---

### Likelihood Explanation

The router is the primary user-facing swap entrypoint in `metric-periphery`. Any pool that deploys `SwapAllowlistExtension` and also wants to support router-based swaps for its allowlisted users **must** allowlist the router, triggering the bypass automatically. The attacker needs no special privileges, no flash loan, and no unusual token behavior — a single `exactInputSingle` call suffices.

---

### Recommendation

`SwapAllowlistExtension` must gate on the **originating user**, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check `recipient` instead of `sender`** (if the pool's design guarantees recipient == originating user for single-hop swaps): This is fragile for multi-hop paths.

3. **Preferred — mirror `DepositAllowlistExtension`'s pattern**: Add a `recipient` check or require the pool to pass the originating user explicitly. The cleanest fix is to have the router encode the real payer/user in `extensionData` and have the extension verify it, similar to how `DepositAllowlistExtension` uses `owner` rather than `sender`.

---

### Proof of Concept

```
Setup:
  - Pool P has SwapAllowlistExtension E configured on beforeSwap.
  - Admin calls E.setAllowedToSwap(P, router, true)   // to enable router swaps for allowlisted users
  - Admin calls E.setAllowedToSwap(P, alice, true)    // alice is the only intended user
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient=bob, ..., extensionData="")
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[P][router] == true  → passes
  5. Swap executes; bob receives output tokens.

Result: bob, a non-allowlisted user, successfully swaps on a curated pool.
Direct pool call by bob (P.swap(...)) would correctly revert because
allowedSwapper[P][bob] == false and the pool is msg.sender to the extension.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
