The call chain is fully traceable. Here is the analysis:

---

## Call Chain Trace

```
User → MetricOmmSimpleRouter::exactInputSingle(params)
         msg.sender = user
         calls pool.swap(recipient, ...) ← msg.sender in pool = ROUTER
              ↓
         MetricOmmPool::swap (whenNotPaused, nonReentrant)
              _beforeSwap(msg.sender=ROUTER, ...)
              ↓
         ExtensionCalling::_beforeSwap
              abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender=ROUTER, ...))
              ↓
         SwapAllowlistExtension::beforeSwap(sender=ROUTER, ...)
              checks: allowedSwapper[msg.sender=pool][sender=ROUTER]
```

The `sender` the hook sees is the **router address**, not the original user.

---

### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension`, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The hook therefore checks the router's allowlist status, not the actual swapper's. If the router is allowlisted for a pool, any unprivileged user can bypass the allowlist entirely by routing through it.

### Finding Description

In `MetricOmmPool::swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When `MetricOmmSimpleRouter::exactInputSingle` (or any `exact*` variant) calls the pool, the pool's `msg.sender` is the router: [4](#0-3) 

The router stores the original user only in transient callback context for payment purposes — it is never forwarded to the pool as the swap initiator. The hook therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**Two concrete failure modes:**

1. **Allowlist bypass**: If the pool admin allowlists the router address (a natural configuration to let market makers use the router), every unprivileged user can swap through the router regardless of their individual allowlist status. The allowlist is completely defeated.

2. **Broken functionality for allowlisted users**: If the pool admin allowlists specific EOAs/contracts but not the router, those allowlisted users cannot use the router at all — their swaps revert with `NotAllowedToSwap` even though they are individually permitted.

**Regarding the pause claim in the question**: The `whenNotPaused` modifier on `swap` reverts before `_beforeSwap` is ever called when `pauseLevel != 0`. There is no "paused pool still exposing a public flow" — the pause aspect of the question is not a real issue. [5](#0-4) 

### Impact Explanation

The allowlist is the primary access-control mechanism for restricted pools. If the router is allowlisted (the only way to let allowlisted users use the router), the gate is open to all users. Any unprivileged attacker can swap in a pool designed to be restricted, potentially draining liquidity at oracle-derived prices that the pool designers only intended to expose to specific counterparties. This constitutes broken core functionality and a constrained loss of LP funds.

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and also wants users to access it via the canonical router will inevitably allowlist the router, triggering the bypass. This is not a corner case — it is the expected operational configuration.

### Recommendation

The extension must verify the **original** user, not the intermediary. Two options:

1. **Pass original sender through router**: The router should forward the original `msg.sender` in `extensionData` or a dedicated field, and the extension should read it from there (requires a protocol-level convention).
2. **Check recipient instead of sender**: For allowlist purposes, gate on `recipient` if the pool is designed so that only the recipient benefits — but this has its own semantic issues.
3. **Preferred**: Add a `trustedForwarder` concept: the extension checks if `sender` is a known router, and if so, reads the real initiator from a signed or transient-storage-backed field set by the router before the call.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the intended allowlisted user
  allowedSwapper[pool][router] = true  // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)  [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → hook checks allowedSwapper[pool][router] == true  ✓
  → swap succeeds for bob, who was never allowlisted
```

Direct call by bob:
```
  bob calls pool.swap(...) directly
  → _beforeSwap(sender=bob, ...)
  → allowedSwapper[pool][bob] == false → revert NotAllowedToSwap  ✓
```

The router path bypasses the check that the direct path enforces.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
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
