The call chain is fully traceable. Here is the definitive analysis.

---

## Call Chain Trace

**Direct swap path:**
```
user → pool.swap(msg.sender=user) → _beforeSwap(sender=user) 
     → SwapAllowlistExtension.beforeSwap(sender=user)
     → allowedSwapper[pool][user]  ✓ correct identity
```

**Router-mediated path:**
```
user → router.exactInputSingle() → pool.swap(msg.sender=router) 
     → _beforeSwap(sender=router)
     → SwapAllowlistExtension.beforeSwap(sender=router)
     → allowedSwapper[pool][router]  ✗ wrong identity
```

The pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the router calls `pool.swap()`, `msg.sender` inside the pool is the **router address**, so `sender` forwarded to the hook is the router, not the original user: [4](#0-3) 

---

## Vulnerability Assessment

The allowlist is structurally broken for router-mediated swaps. There are exactly two outcomes, both bad:

**Outcome A — Router is NOT allowlisted:**
Allowlisted users cannot use the router at all. The router is a core piece of periphery infrastructure (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`). Blocking it silently breaks the intended user experience for every allowlisted address.

**Outcome B — Router IS allowlisted (the bypass):**
Any unprivileged user calls `router.exactInputSingle()` targeting the restricted pool. The hook sees `sender = router`, which is allowlisted, and passes. The original user's identity is never checked. The allowlist is completely bypassed by any address that can call the public router.

The router is a public, permissionless contract. There is no mechanism inside the router to restrict which users can initiate swaps through it. Once the router address is in `allowedSwapper`, the gate is open to everyone.

The "stop-loss" framing in the question is noise — `SwapAllowlistExtension` has no watermark or drawdown logic. But the identity-confusion bug is real and independently sufficient.

---

### Title
Router-mediated swaps bypass `SwapAllowlistExtension` by substituting the router address for the original swapper identity — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the hook checks the router's allowlist status rather than the user's. Any user can bypass a per-user allowlist by calling the public router if the router address is allowlisted.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap` uses this value to look up `allowedSwapper[msg.sender][sender]` (pool → swapper). When the swap originates from `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. The allowlist therefore cannot distinguish between different users routing through the same router instance. If the router is allowlisted (required for any allowlisted user to use periphery swap functions), the gate is open to all callers of the router.

### Impact Explanation
Any address can swap in a pool that the admin intended to restrict to specific counterparties. Pools using `SwapAllowlistExtension` for access control (e.g., private market-making pools, pools with restricted counterparty sets) are fully open to the public via the router. This constitutes broken core pool functionality and enables unauthorized bad-price or value-leak execution that the configured protection was supposed to stop.

### Likelihood Explanation
The router is the standard user-facing entry point for swaps. Any pool that (a) uses `SwapAllowlistExtension` and (b) needs allowlisted users to access the router must allowlist the router address, at which point the bypass is trivially available to every address on-chain. No special privileges, flash loans, or oracle manipulation are required.

### Recommendation
The `sender` passed to extensions must reflect the true originating user, not the immediate caller. Options:

1. **Pass `tx.origin` as an additional parameter** alongside `sender` so extensions can choose which identity to gate on (though `tx.origin` has its own risks in meta-transaction contexts).
2. **Require the router to forward the original user identity** via `extensionData`, and have `SwapAllowlistExtension` decode and verify it — but this is unauthenticated and spoofable.
3. **The correct fix:** gate the allowlist on `tx.origin` when `sender` is a known router, or redesign the extension interface to carry an authenticated originator field that the pool populates from a trusted source.
4. **Alternatively:** document that `SwapAllowlistExtension` only gates direct pool callers and is incompatible with router use, and provide a separate router-aware allowlist extension.

### Proof of Concept

```solidity
// Setup: pool admin allowlists only `trustedUser` and the router
// (router must be allowlisted for trustedUser to use periphery)
swapAllowlist.setAllowedToSwap(pool, trustedUser, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attack: attacker (not allowlisted) calls router directly
// router.exactInputSingle() → pool.swap(msg.sender=router)
// hook sees sender=router → allowedSwapper[pool][router]=true → PASSES
// Attacker swaps successfully in a pool they were never authorized to access.

vm.prank(attacker); // attacker is NOT in allowedSwapper
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    tokenIn: token0,
    recipient: attacker,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Succeeds — allowlist bypassed
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
