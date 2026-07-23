The call chain is fully traceable. Here is the analysis:

---

## Call Chain Trace

```
User → MetricOmmSimpleRouter.exactInputSingle()
         └─ pool.swap(recipient, zeroForOne, amount, ..., extensionData)
              │  msg.sender = router address
              └─ _beforeSwap(msg.sender=router, ...)   [MetricOmmPool.sol:230]
                   └─ extension.beforeSwap(sender=router, ...)
                        └─ allowedSwapper[msg.sender=pool][sender=router]  ← WRONG IDENTITY
```

---

### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension`, Defeating Per-User Allowlist Gating — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address the pool received as its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the user. The hook therefore checks whether the **router** is allowlisted, not the actual economic actor.

### Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` directly: [4](#0-3) 

So `sender` = router address, and the check becomes `allowedSwapper[pool][router]` — the actual user's address is never examined.

### Impact Explanation

Two mutually exclusive failure modes arise:

**Mode A — Allowlist bypass (higher impact):** If the pool admin allowlists the router so that legitimate users can swap through it, then *any* address can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The allowlist is completely defeated for all router-mediated swaps.

**Mode B — Allowlisted users blocked:** If the pool admin allowlists specific user addresses but not the router, those users cannot use the router at all — they must call the pool directly. This breaks the expected UX and core router functionality for allowlisted pools.

There is no configuration that simultaneously allows "only specific users may swap" and "those users may use the router." The invariant the pool admin intended — that only allowlisted actors can trade — cannot be enforced across the public router entrypoint.

Impact: **Medium — broken core functionality** of the `SwapAllowlistExtension` when combined with the router, which is the standard public entrypoint.

Note: The "paused-flow" framing in the question is not a separate vector. The `swap()` function carries a `whenNotPaused` modifier that reverts before any extension is called, so paused pools do not expose a live swap flow. [5](#0-4) 

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects to gate swaps to specific addresses is affected the moment users interact through the router. The router is the standard public interface; most users will use it. The misconfiguration is structural, not accidental.

### Recommendation

The extension needs the **original user's address**, not the immediate caller of `pool.swap()`. Options:

1. **Pass the original sender through `extensionData`**: The router encodes `msg.sender` (the user) into `extensionData` before calling the pool. The extension decodes and checks it. This requires a trusted encoding convention.
2. **Add an `originalSender` field to the hook signature**: The pool could accept an explicit `originalSender` parameter distinct from the immediate `msg.sender`, populated by the router.
3. **Allowlist at the router level**: The router enforces the allowlist before calling the pool, and the pool's extension trusts the router. This requires the router to be a trusted, non-upgradeable contract.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for legitimate users.
3. An unprivileged address (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The pool receives `msg.sender = router`, calls `beforeSwap(sender=router, ...)`.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The unprivileged user successfully swaps on an allowlisted pool, bypassing the per-user gate entirely.

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
