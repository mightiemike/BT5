### Title
`SwapAllowlistExtension.beforeSwap` checks the direct pool caller (`sender`) instead of the actual swapper, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap()` — i.e., `msg.sender` of the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the user. If the pool admin allowlists the router address (a natural step to enable router-based trading on a curated pool), every user — including those not individually allowlisted — can bypass the per-user swap gate.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`** [1](#0-0) 

The extension checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool.

**What the pool forwards as `sender`** [2](#0-1) 

The pool passes `msg.sender` — the direct caller of `pool.swap()` — as `sender` to every extension hook.

**What the router passes as `msg.sender` to the pool** [3](#0-2) 

`exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The pool therefore sees `msg.sender = router`, so `sender` forwarded to the extension is the **router address**, not the end user.

**Contrast with `DepositAllowlistExtension`** [4](#0-3) 

The deposit extension correctly checks `owner` (the second parameter — the actual position owner), which the liquidity adder always sets to the real user. The swap extension has no equivalent "owner" field and instead relies on `sender`, which collapses to the router address for all router-originated swaps.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (to let approved users trade via the standard periphery) inadvertently opens the pool to **all** users. Any address can call `router.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` targeting the curated pool; the extension sees `sender = router`, finds the router allowlisted, and permits the swap. The per-user allowlist is completely nullified. This is a curation failure: disallowed users can trade on a pool that was explicitly configured to restrict access, matching the "High direct loss or curation failure if disallowed users can still trade" impact gate.

---

### Likelihood Explanation

The trigger is a semi-trusted pool admin action: allowlisting the router. This is the natural, expected step for any admin who wants approved users to be able to use the standard periphery rather than calling the pool directly. The admin has no on-chain signal that allowlisting the router is semantically equivalent to `setAllowAllSwappers(pool, true)`. The design asymmetry (deposit extension checks `owner`; swap extension checks `sender`) makes the mistake non-obvious. Likelihood is **Medium**.

---

### Recommendation

The `SwapAllowlistExtension` should not rely on `sender` (the direct pool caller) as the identity to gate. Two viable fixes:

1. **Require the router to embed the real user in `extensionData`** and have the extension decode and check that address when `sender` is a known router.
2. **Check `recipient` instead of `sender`** — the recipient is the address that economically benefits from the swap and is always set by the originating user, even through the router.

Alternatively, document explicitly that allowlisting the router is equivalent to opening the pool to all users, and provide a separate "router-aware" extension that decodes the real swapper from `extensionData`.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to let approved users use the router
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is individually approved
  bob is NOT individually approved

Attack:
  bob calls router.exactInputSingle({pool: curatedPool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls extension.beforeSwap(router, recipient, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob despite bob not being allowlisted
```

The invariant "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" is broken: direct pool calls check the real user; router calls check the router.

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
