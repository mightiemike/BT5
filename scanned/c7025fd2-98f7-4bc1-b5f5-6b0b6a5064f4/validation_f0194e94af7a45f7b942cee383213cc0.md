Let me check the `ExtensionCalling` contract to confirm how `_beforeSwap` passes the sender to the hook.

The call chain is fully traceable. Here is the verdict:

---

### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension::beforeSwap`, Breaking Allowlist Identity Gating — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address passed by the pool. The pool always passes its own `msg.sender` as `sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the user. The hook therefore checks whether the router is allowlisted, not the actual user, making the allowlist ungatable for router-mediated swaps.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

In `MetricOmmPool::swap`, the call to `_beforeSwap` always uses `msg.sender`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` then encodes that value as the first argument to the extension call: [2](#0-1) 

**Step 2 — The router is the pool's `msg.sender`.**

`MetricOmmSimpleRouter::exactInputSingle` calls `pool.swap(...)` directly: [3](#0-2) 

So when a user calls the router, the pool sees `msg.sender = router`. The `sender` forwarded to `beforeSwap` is the **router address**, not the originating user.

**Step 3 — The hook checks the wrong identity.**

`SwapAllowlistExtension::beforeSwap` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert NotAllowedToSwap();
}
``` [4](#0-3) 

Here `msg.sender` = pool (correct), `sender` = router (wrong — should be the user). The check resolves to `allowedSwapper[pool][router]`.

**Two concrete failure modes:**

| Scenario | Result |
|---|---|
| Pool admin allowlists the router to enable router swaps | Every user can bypass the allowlist by routing through the router |
| Pool admin does not allowlist the router | Allowlisted users cannot use the router at all — broken functionality |

**Note on the "paused pool" framing in the question:** This is a red herring. `swap()` applies `whenNotPaused` before `_beforeSwap` is ever reached: [5](#0-4) 

A paused pool reverts at the modifier; the hook is never invoked. The pause path does not interact with this bug.

### Impact Explanation

The `SwapAllowlistExtension` is designed to restrict swaps to a specific set of addresses per pool. When the router is involved, that restriction is either completely bypassed (if the router is allowlisted) or incorrectly applied to allowlisted users (if the router is not allowlisted). Pool designers who deploy this extension to gate institutional or permissioned pools cannot enforce the intended access control for any user who routes through `MetricOmmSimpleRouter`. This is broken core functionality of the extension.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap interface. Any allowlisted user who naturally uses the router (the expected UX path) will find their swap reverted. Any pool admin who allowlists the router to fix that will inadvertently open the pool to all users. The misconfiguration is a near-certain operational outcome.

### Recommendation

The `beforeSwap` hook should not rely on the `sender` parameter alone to identify the actual swapper. Options:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension reads and verifies it. This requires a trusted router check.
2. **Allowlist at the router level**: The extension checks `allowedSwapper[pool][sender]` and also accepts a router-signed proof of the originating user.
3. **Document that the extension is incompatible with the router**: Pool admins must be warned not to use `SwapAllowlistExtension` on pools accessible via `MetricOmmSimpleRouter`.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; allowlist only `alice`.
2. Pool admin also allowlists the router (to let alice use it).
3. Bob (not allowlisted) calls MetricOmmSimpleRouter::exactInputSingle targeting the pool.
4. Router calls pool.swap(...); pool's msg.sender = router.
5. beforeSwap receives sender = router; allowedSwapper[pool][router] = true → passes.
6. Bob's swap executes despite not being allowlisted.
```

Alternatively, without step 2: Alice calls the router; `allowedSwapper[pool][router] = false` → reverts, even though Alice is allowlisted.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
