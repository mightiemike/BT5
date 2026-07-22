### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the **router contract**, not the end user. The allowlist therefore gates the router address rather than the actual swapper. If the pool admin allowlists the router to permit legitimate users to trade through it, every non-allowlisted address can bypass the curated-pool restriction by routing through the public, permissionless router.

### Finding Description

**Call chain for a router-mediated swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
         └─ pool.swap(params.recipient, ...)   // msg.sender = router
               └─ _beforeSwap(msg.sender=router, ...)
                     └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                           └─ allowedSwapper[pool][router]  ← wrong actor
```

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

The router calls `pool.swap(params.recipient, ...)` directly, making itself `msg.sender` of the pool call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The allowlist is keyed `pool → swapper → bool`, but the swapper slot is filled with the router address, not the end user's address. The same misbinding occurs for all router entry points (`exactInput`, `exactOutputSingle`, `exactOutput`): [4](#0-3) 

### Impact Explanation

Two fund-impacting outcomes arise from the wrong-actor binding:

**Outcome A — Allowlist bypass (High):** The pool admin must allowlist the router address to let legitimate users trade through the supported periphery path. Once the router is allowlisted, `allowedSwapper[pool][router] = true` passes for every caller of the router, including addresses the admin explicitly never allowlisted. Any non-KYC'd or otherwise excluded address can call `router.exactInputSingle(...)` and the guard silently passes. The curated-pool invariant is fully defeated.

**Outcome B — Broken core swap flow (Medium):** If the pool admin does not allowlist the router, every allowlisted user who tries to swap through the router is blocked (`sender = router`, not in allowlist). Legitimate users are forced to implement `IMetricOmmSwapCallback` themselves and call the pool directly, making the supported periphery path unusable for any pool that deploys `SwapAllowlistExtension`.

Both outcomes are reachable by any unprivileged user with no special setup beyond calling the public router.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed by the protocol. Any pool that combines `SwapAllowlistExtension` with the router — the natural production configuration — immediately exhibits the wrong-actor binding. No special timing, oracle manipulation, or privileged access is required; a single `exactInputSingle` call from a non-allowlisted address is sufficient to demonstrate the bypass.

### Recommendation

The extension must check the identity of the economic actor, not the immediate pool caller. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated convention between the router and the extension.

2. **Check `sender` only when it is not a known router:** The pool admin maintains a separate router registry; the extension falls back to checking `extensionData`-supplied identity when `sender` is a registered router.

The simplest safe default is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert with a clear error when `sender` is not an EOA, or to require that pools using this extension only accept direct pool calls.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap hook active)
  pool admin: allowedSwapper[pool][alice] = true
              allowedSwapper[pool][router] = true  ← required for alice to use router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})
      → pool.swap(bob, ...)  [msg.sender = router]
        → beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  → PASSES

Result:
  bob executes a swap on a curated pool that was supposed to block him.
  The allowlist check never evaluated bob's address.

Verification (direct call, correctly blocked):
  bob calls pool.swap(...) directly
    → beforeSwap(sender=bob, ...)
      → allowedSwapper[pool][bob] == false → REVERTS NotAllowedToSwap ✓
```

The root cause is that `SwapAllowlistExtension.beforeSwap` receives `sender = router` from the pool, while the pool's `swap()` unconditionally forwards `msg.sender` as the sender argument, with no mechanism to propagate the original end-user identity through the router hop. [5](#0-4) [6](#0-5) [7](#0-6)

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
