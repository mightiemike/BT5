The call chain is fully traceable. Here is the complete analysis:

---

## Call Chain Trace

**Direct swap path:**
`user` → `pool.swap()` → `_beforeSwap(msg.sender=user, ...)` → `extension.beforeSwap(sender=user, ...)` → checks `allowedSwapper[pool][user]`

**Router swap path:**
`user` → `router.exactInputSingle()` → `pool.swap()` → `_beforeSwap(msg.sender=router, ...)` → `extension.beforeSwap(sender=router, ...)` → checks `allowedSwapper[pool][router]`

The pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the router is the caller, `sender = router_address`, not the original user.

---

## Pause Angle: Not Valid

The `swap` function carries `whenNotPaused` which reverts before `_beforeSwap` is ever reached: [4](#0-3) 

There is no path where a paused pool reaches the extension hook. The "paused-flow regression" framing in the question is incorrect.

---

## Real Issue: Sender Identity Mismatch

The sender identity mismatch produces two concrete effects:

**Effect 1 — Allowlisted users blocked via router (broken functionality):**
Pool admin allowlists `alice`. Alice calls `router.exactInputSingle(...)`. The router calls `pool.swap(...)`, so `sender = router`. The check `allowedSwapper[pool][router]` is false → Alice's swap reverts even though she is explicitly allowlisted. This is demonstrably broken core functionality.

**Effect 2 — Allowlist bypass if router is allowlisted:**
If the pool admin allowlists the router address (a natural step to "allow router users"), then `allowedSwapper[pool][router] = true` and every user — including those the admin never intended to allowlist — can bypass the per-user gate by routing through the router. This is an admin-boundary break reachable by any unprivileged user.

The router stores the original `msg.sender` only in transient callback context for payment purposes, not for identity forwarding to the pool: [5](#0-4) 

There is no mechanism by which the router communicates the original user's address to the pool's extension hook.

---

### Title
Router-mediated swaps substitute the router address for the original user in `SwapAllowlistExtension.beforeSwap`, breaking per-user allowlist enforcement — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the originating user. This breaks the allowlist in both directions: allowlisted users are blocked when using the router, and if the router itself is allowlisted, any user bypasses the per-user gate.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension. `SwapAllowlistExtension.beforeSwap` uses this value as the swapper identity to check against `allowedSwapper[pool][sender]`. When `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` calls `pool.swap(...)`, `msg.sender` inside the pool is the router contract, not the end user. The router stores the original caller only in transient storage for the payment callback and never passes it to the pool as an identity parameter. Consequently, the extension sees `sender = router` for every router-mediated swap.

### Impact Explanation
- **Broken core functionality (certain):** Any pool using `SwapAllowlistExtension` with a non-`allowAll` configuration is unusable via the router for allowlisted users. Allowlisted users who attempt to swap through the router receive `NotAllowedToSwap` reverts.
- **Allowlist bypass (conditional but unprivileged):** If the pool admin allowlists the router address — a natural operational step to permit router access — every unprivileged user can bypass the per-user allowlist by routing through the router. The bypass requires no special privilege beyond calling the public router.

### Likelihood Explanation
The router is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` with per-user restrictions and expects users to interact via the router will immediately exhibit the broken-functionality impact. The bypass scenario follows directly from the natural admin response to the first impact.

### Recommendation
`SwapAllowlistExtension.beforeSwap` should not rely solely on the `sender` argument for identity. Options:
1. Have the router pass the original user's address in `extensionData`, and have the extension decode and verify it (requires a trusted router registry or signature).
2. Alternatively, the pool/extension framework should propagate `tx.origin` or a verified originator alongside `sender` — though `tx.origin` has its own risks.
3. The cleanest fix: add a router-aware path where the router is a trusted forwarder that encodes the real user in `extensionData`, and the extension verifies the router's identity before trusting the forwarded address.

### Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension; set allowAllSwappers[pool] = false.
2. Call setAllowedToSwap(pool, alice, true).
3. Alice calls router.exactInputSingle({pool: pool, ...}) — reverts with NotAllowedToSwap
   because pool sees msg.sender = router, and allowedSwapper[pool][router] = false.

4. Admin calls setAllowedToSwap(pool, router, true) to "fix" router access.
5. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...}) — succeeds,
   because allowedSwapper[pool][router] = true covers all router callers.
   Bob has bypassed the per-user allowlist.
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
