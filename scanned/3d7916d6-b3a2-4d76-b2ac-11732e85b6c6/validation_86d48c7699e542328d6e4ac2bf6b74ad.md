The call chain is fully traceable. Here is the exact analysis:

## Call Flow Trace

```
User → MetricOmmSimpleRouter::exactInputSingle
         → IMetricOmmPoolActions(pool).swap(...)   [msg.sender = router]
           → MetricOmmPool::swap
               → _beforeSwap(msg.sender=router, ...)
                 → ExtensionCalling::_beforeSwap encodes sender=router
                   → SwapAllowlistExtension::beforeSwap(sender=router, ...)
                       checks: allowedSwapper[msg.sender=pool][sender=router]
```

**The `sender` passed to the hook is always the router address, never the original user.**

---

### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension::beforeSwap`, Breaking Per-User Allowlist Gating — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address the pool received as `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the original user. The hook therefore checks whether the **router** is allowlisted, not the individual user.

### Finding Description

In `MetricOmmPool::swap`, the pool calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` encodes this `sender` and forwards it to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = pool, `sender` = router. The check is `allowedSwapper[pool][router]`, never `allowedSwapper[pool][originalUser]`.

The router calls the pool with `msg.sender` = router in all `exact*` entry points: [4](#0-3) [5](#0-4) 

### Impact Explanation

The pool admin faces an impossible choice:

- **If the router is NOT allowlisted**: Every allowlisted user is blocked from using the router. Core swap functionality is broken for the intended audience.
- **If the router IS allowlisted**: Every user on the network can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. The allowlist is completely defeated — any unprivileged address can swap on a pool that was intended to be restricted.

The second case is the direct exploit path: an unprivileged attacker calls `exactInputSingle` or `exactOutputSingle` on a pool whose admin allowlisted the router to enable router-mediated swaps for legitimate users. The attacker's address is never checked; only the router's address is.

### Likelihood Explanation

Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router to support normal UX is fully bypassed. This is the expected production configuration — pools that use an allowlist but also want to support the official router must allowlist it, which opens the bypass to all users.

### Recommendation

The pool must receive the original user's address, not the router's. Options:
1. Pass the original user address through `extensionData` and have the extension decode and verify it (with the router signing or committing to it).
2. Have the router store the original `msg.sender` in transient storage and expose a view that the extension can read during the hook call.
3. Require direct pool interaction (no router) for allowlisted pools — but this breaks UX.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter::exactInputSingle` targeting the pool.
4. Pool calls `_beforeSwap(msg.sender=router, ...)`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes on a pool intended to be restricted.

**Note on the question's framing**: The question references a "velocity-envelope bypass" and "per-block price-change cap." `SwapAllowlistExtension` contains no such logic — it is a pure allowlist with no price or velocity guard. The actual vulnerability is the identity mismatch described above, which is real and independently provable.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
