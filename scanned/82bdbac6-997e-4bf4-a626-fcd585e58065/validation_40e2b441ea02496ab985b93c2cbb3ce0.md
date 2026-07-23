### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual end-user, making the allowlist ineffective for router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured extension. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, so it evaluates the router's allowlist status, not the real swapper's. This produces two concrete broken invariants simultaneously: (1) allowlisted users are silently blocked from using the router, and (2) if the pool admin allowlists the router address to restore router access, every user on the internet bypasses the allowlist.

### Finding Description

**Pool passes `msg.sender` as `sender` to every extension:** [1](#0-0) 

```solidity
_beforeSwap(
  msg.sender,   // ← router address when called through the router
  recipient,
  ...
  extensionData
);
```

**`SwapAllowlistExtension` checks `sender` (= router) against the allowlist:** [2](#0-1) 

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
```

`msg.sender` here is the pool; `sender` is whatever the pool forwarded — the router address for every `MetricOmmSimpleRouter` call.

**Router calls `pool.swap` with itself as `msg.sender`:** [3](#0-2) 

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` (including the recursive callback path at line 220). [4](#0-3) 

### Impact Explanation

**Broken invariant A — allowlisted users cannot use the router:**
A pool admin allowlists `userA`. `userA` calls `router.exactInputSingle`. The pool passes `sender = router`. The extension checks `allowedSwapper[pool][router]` → `false` → reverts `NotAllowedToSwap`. `userA` is permanently locked out of the supported periphery path despite being individually allowlisted. This is a broken core swap flow for every curated pool that uses `SwapAllowlistExtension`.

**Broken invariant B — allowlist bypass when the router is allowlisted:**
To restore router access, the pool admin adds the router to the allowlist (`allowedSwapper[pool][router] = true`). Now `allowedSwapper[pool][router]` is `true`, so the extension passes for every caller who routes through the router — including addresses that were never individually allowlisted. The entire curation policy collapses: any user can bypass the allowlist by calling `router.exactInputSingle` instead of `pool.swap` directly.

Both impacts are reachable through normal, unprivileged user actions on any pool that has `SwapAllowlistExtension` wired as a `beforeSwap` hook.

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery extension explicitly documented as the mechanism for curated pools. Any pool that deploys it and expects users to interact through the router (the primary supported periphery path) immediately hits invariant A. Invariant B follows as soon as the admin takes the natural remediation step of allowlisting the router. No special token behavior, no malicious setup, and no privileged attacker role is required.

### Recommendation

The extension must gate the economically relevant actor, not the immediate `pool.swap` caller. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the real user (`msg.sender`) into `extensionData` for every hop, and document this as the canonical way to pass user identity to extensions.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the real user from `extensionData` when present, falling back to `sender` only for direct pool calls. Alternatively, add a separate `allowedRouter` mapping so the extension can distinguish "this router is trusted to forward allowlisted users" from "this address is an allowlisted swapper."

### Proof of Concept

**Setup:**
- Pool deployed with `SwapAllowlistExtension` as `beforeSwap` hook.
- Pool admin allowlists `alice` directly: `setAllowedToSwap(pool, alice, true)`.

**Step 1 — Broken functionality (invariant A):**
```
alice → router.exactInputSingle({pool, ...})
  router → pool.swap(recipient, ...)   // msg.sender = router
    pool → extension.beforeSwap(sender=router, ...)
      allowedSwapper[pool][router] == false → revert NotAllowedToSwap
```
Alice is allowlisted but cannot use the router.

**Step 2 — Admin remediation that opens the bypass (invariant B):**
```
admin → setAllowedToSwap(pool, router, true)
```

**Step 3 — Bypass by non-allowlisted user:**
```
eve (not allowlisted) → router.exactInputSingle({pool, ...})
  router → pool.swap(recipient, ...)   // msg.sender = router
    pool → extension.beforeSwap(sender=router, ...)
      allowedSwapper[pool][router] == true → passes
```
Eve trades on the curated pool without ever being individually allowlisted, violating the pool's access-control invariant. [2](#0-1) [1](#0-0) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
