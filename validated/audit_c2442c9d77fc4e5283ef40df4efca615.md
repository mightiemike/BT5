Audit Report

## Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension`, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the router is allowlisted for a pool, any unprivileged user can bypass the allowlist entirely by routing through it, defeating the primary access-control mechanism for restricted pools.

## Finding Description

In `MetricOmmPool::swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that `sender` verbatim to the extension via `abi.encodeCall`. `SwapAllowlistExtension::beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [2](#0-1) 

When `MetricOmmSimpleRouter::exactInputSingle` calls the pool, the original user's address is stored only in transient callback context for payment — it is never forwarded to the pool as the swap initiator: [3](#0-2) 

The pool therefore receives `msg.sender = router`, and the hook evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`.

Two concrete failure modes result:
1. **Allowlist bypass**: If the pool admin allowlists the router (the natural configuration to let allowlisted users use the router), every unprivileged user can swap through the router regardless of their individual allowlist status.
2. **Broken functionality for allowlisted users**: If the admin allowlists specific EOAs but not the router, those users cannot use the router at all — their swaps revert with `NotAllowedToSwap` even though they are individually permitted.

## Impact Explanation

The allowlist is the primary access-control mechanism for restricted pools. If the router is allowlisted (the only way to let allowlisted users use the router), the gate is open to all users. Any unprivileged attacker can swap in a pool designed to be restricted, potentially draining liquidity at oracle-derived prices that the pool designers only intended to expose to specific counterparties. This constitutes broken core pool functionality and a constrained loss of LP funds.

## Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and also wants users to access it via the canonical router will inevitably allowlist the router, triggering the bypass. This is not a corner case — it is the expected operational configuration. The attacker requires no special privileges, only the ability to call the public router.

## Recommendation

The extension must verify the original user, not the intermediary. The preferred fix is to add a `trustedForwarder` concept: the extension checks if `sender` is a known router, and if so, reads the real initiator from a signed or transient-storage-backed field set by the router before the call. Alternatively, the router should forward the original `msg.sender` in `extensionData` under a protocol-level convention, and the extension should read it from there when `sender` is a recognized router address.

## Proof of Concept

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

Direct call by bob:
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
