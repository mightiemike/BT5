### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any unprivileged swapper to bypass a curated pool's allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every unprivileged address can bypass the allowlist by routing through the router.

---

### Finding Description

**Pool `swap` passes `msg.sender` (the router) as `sender` to the extension:** [1](#0-0) 

```solidity
_beforeSwap(
  msg.sender,   // ← always the router when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`:** [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the router is the caller, the check becomes `allowedSwapper[pool][router]`.

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender`:** [3](#0-2) 

The router never forwards the original `msg.sender` (the end user) to the pool; it is always the router address that the pool sees.

**The allowlist admin API operates on individual swapper addresses:** [4](#0-3) 

The design intent is per-user gating, but the runtime check collapses all router users into a single identity.

---

### Impact Explanation

Two broken states arise:

1. **Allowlist bypass (critical path):** The pool admin allowlists the router so that legitimate users can swap through it. Because the extension sees only the router address, every non-allowlisted address can now swap on the curated pool by calling `router.exactInputSingle()`. The allowlist is completely defeated for all router-mediated swaps.

2. **Legitimate users blocked:** If the admin does *not* allowlist the router (to preserve the allowlist), individually allowlisted users cannot use the router at all, breaking the standard swap UX for the pool.

Both outcomes represent broken core pool functionality: either the allowlist guard is bypassed (direct loss of curation policy, potential unauthorized trading on restricted pools) or the pool is effectively unusable via the supported periphery path for all allowlisted users.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary supported swap entry point for end users.
- Any pool that deploys `SwapAllowlistExtension` and also wants router support must allowlist the router, triggering the bypass.
- No special privileges or unusual conditions are required; any unprivileged address can call `router.exactInputSingle()`.
- The multi-hop `exactInput` path has the same issue for every hop. [5](#0-4) 

---

### Recommendation

The extension must receive and check the **original end-user address**, not the intermediary router. Two complementary fixes:

1. **Pass the original initiator through the pool:** Add an `initiator` field to the `beforeSwap` hook signature (or a separate `extensionData` convention) so the router can forward `msg.sender` (the actual user) to the extension.

2. **Check `sender` against the allowlist only when `sender` is not a trusted router; otherwise check the payer stored in transient context:** The router already stores the original payer in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`). The extension could read this via a standardized interface.

The simplest correct fix is for the pool to accept an explicit `initiator` address from the caller and pass it to extensions as the actor to gate, while keeping `sender` (the direct caller) for callback-security purposes.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // must do this for router to work
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...)  →  msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router]  →  true  →  passes
  - attacker's swap executes on the curated pool despite never being allowlisted

Result:
  - attacker receives output tokens from a pool that was supposed to restrict swaps
    to a curated set of addresses; the allowlist invariant is broken.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
