Let me check the `ExtensionCalling` contract to confirm how `_beforeSwap` passes the sender to the extension hook.

The call chain is fully traceable. Here is the complete analysis:

---

### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension::beforeSwap`, Breaking Allowlist Identity Invariant — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address the pool received as `msg.sender` when `swap()` was called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the original user. The hook therefore checks whether the **router** is allowlisted, not the actual trader. This corrupts the swapper identity the pool admin intended to gate.

The pause-related framing in the question is invalid: `MetricOmmPool::swap` carries a `whenNotPaused` modifier and reverts on any non-zero `pauseLevel`, so no paused-pool bypass exists.

---

### Finding Description

**Call chain:**

```
user → MetricOmmSimpleRouter::exactInputSingle()
         └─ pool.swap(recipient, ...) [msg.sender = router]
               └─ _beforeSwap(msg.sender=router, ...)   [ExtensionCalling.sol:160-176]
                     └─ SwapAllowlistExtension::beforeSwap(sender=router, ...)
                           └─ allowedSwapper[pool][router]  ← wrong identity
```

**Pool passes its own `msg.sender` to the hook:**

`MetricOmmPool::swap` calls `_beforeSwap(msg.sender, ...)` at line 231. [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that value as the first argument to every configured extension: [2](#0-1) 

**The hook checks the wrong address:**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (correct), `sender` = router (wrong — should be the original user). [3](#0-2) 

**The router sets `msg.sender` as the payer but never passes it to the pool:**

`exactInputSingle` stores the original caller for the payment callback but calls `pool.swap()` directly — the pool only sees the router as caller. [4](#0-3) 

---

### Impact Explanation

Two concrete broken invariants result:

**1. Allowlisted users are silently locked out of the router.**
A pool admin allowlists `alice`. Alice calls `exactInputSingle` → pool sees `sender = router` → `allowedSwapper[pool][router]` is `false` → revert. Alice can only swap by calling the pool directly. The router — the primary public interface — is unusable for any allowlisted pool.

**2. If the router is allowlisted to fix (1), the allowlist is fully bypassed.**
A pool admin who allowlists the router address to restore router compatibility inadvertently grants every user on the network swap access, defeating the entire purpose of the extension. Any unprivileged address can call `exactInputSingle` and pass the hook.

Both outcomes constitute broken core functionality: the allowlist extension cannot correctly gate router-mediated swaps under any configuration.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap interface. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router will immediately exhibit one of the two failure modes above. No special attacker setup is required — the misbinding is structural and triggered by every router call.

---

### Recommendation

The pool must propagate the original caller's identity through the swap path. Two options:

1. **Pass original sender via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and uses it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Preferred — add a `tx.origin`-free forwarding field**: Extend the pool's `swap` signature with an explicit `swapper` parameter that the pool passes to hooks. The pool validates that `swapper == msg.sender` for direct calls, and the router passes the original user. This preserves composability without relying on `tx.origin`.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, alice is allowlisted
allowedSwapper[pool][alice] = true;

// Alice tries to swap via router (normal user flow)
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: alice,
    tokenIn: token0,
    amountIn: 1e18,
    ...
}));
// Pool calls _beforeSwap(msg.sender=router, ...)
// Hook checks allowedSwapper[pool][router] → false → REVERT: NotAllowedToSwap
// Alice is blocked despite being explicitly allowlisted.

// Admin "fixes" it by allowlisting the router:
allowedSwapper[pool][router] = true;

// Now bob (not allowlisted) swaps via router:
router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
// Hook checks allowedSwapper[pool][router] → true → PASSES
// Bob bypasses the allowlist entirely.
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
