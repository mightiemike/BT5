### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension.beforeSwap`, Breaking Per-User Allowlist Semantics — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address passed by the pool as the caller of `swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The hook therefore checks whether the **router** is allowlisted, not the actual swapper. This breaks the allowlist in both directions: allowlisted users are blocked when they use the router, and any user can bypass a per-user allowlist if the router address is allowlisted.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)` — `msg.sender` = user EOA.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(...)` — pool sees `msg.sender` = **router address**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — passes the **router address** as `sender`. [1](#0-0) 

4. `ExtensionCalling._beforeSwap` encodes `sender` = router address and dispatches to the extension. [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` receives `sender` = router address and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = pool address, `sender` = **router address**. The actual end user's address is never visible to the hook.

The router sets up its callback context with `msg.sender` (the user) stored in transient storage for payment purposes, but it never forwards the user's identity to the pool's `swap` call: [4](#0-3) 

---

### Impact Explanation

Two concrete broken behaviors:

1. **Allowlisted users are blocked via the router.** A pool admin allowlists `alice`. Alice calls `exactInputSingle` through the router. The hook sees `sender = router`, which is not in `allowedSwapper[pool]`, so the swap reverts. Alice can only swap by calling the pool directly — the router is unusable for her.

2. **Allowlist bypass.** A pool admin allowlists the router address (e.g., to let "any router user" in). Now every address in the world can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. The allowlist provides no per-user access control.

The pause-related claim in the question is **not valid**: `MetricOmmPool.swap` carries the `whenNotPaused` modifier, so the hook is never reached on a paused pool. [5](#0-4) 

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects per-user access control is affected the moment a user or integration uses the router. The router is the standard periphery entry point, so this is the expected path for most users.

---

### Recommendation

The pool must forward the original initiator's address, not `msg.sender`, to the extension hook. One standard approach is to have the router pass the original user address inside `extensionData`, and have the extension decode it. Alternatively, the pool could accept an explicit `originator` parameter. The allowlist mapping and `setAllowedToSwap` admin interface would then key on the originator rather than the immediate caller.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; allowAllSwappers[pool] = false.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
3. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
4. Router calls pool.swap(...) — pool's msg.sender = router.
5. beforeSwap receives sender = router.
6. allowedSwapper[pool][router] == false → revert NotAllowedToSwap().
7. Alice's swap fails despite being explicitly allowlisted.

Bypass variant:
1. Pool admin calls setAllowedToSwap(pool, router, true).
2. Bob (not allowlisted) calls exactInputSingle through the router.
3. beforeSwap receives sender = router → allowed → swap succeeds.
4. Bob bypasses the per-user allowlist.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
