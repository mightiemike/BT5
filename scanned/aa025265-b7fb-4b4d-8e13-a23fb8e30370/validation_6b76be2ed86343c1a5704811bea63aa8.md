### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the actual user. A pool admin who allowlists the router to enable router-based swaps on a curated pool inadvertently opens the gate to every user, completely collapsing the per-user allowlist.

---

### Finding Description

**Call chain for a router-mediated swap:**

```
user (alice) → MetricOmmSimpleRouter.exactInputSingle(...)
    → pool.swap(recipient, ...)          // msg.sender = router
        → _beforeSwap(msg.sender=router, ...)
            → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                → checks allowedSwapper[pool][router]   // ← wrong actor
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), but `sender` is the **router**, not the end user. The check becomes `allowedSwapper[pool][router]`.

**Bypass path:** A pool admin who wants allowlisted users to be able to trade via the standard router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, every user who calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) passes the guard, regardless of whether they are individually allowlisted.

**Denial path (secondary):** If the admin allowlists individual users but not the router, those users cannot swap through the router at all — the router's address fails the check — forcing them to call the pool directly, which is not the intended UX for a periphery-enabled protocol.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of participants loses that restriction entirely the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool's liquidity. This is a direct loss of the access-control invariant the pool admin configured, and it exposes LP funds to trades from actors the pool was explicitly designed to exclude.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Pool admins who deploy a curated pool with a swap allowlist and also want to support the router (the expected user-facing path) will naturally allowlist the router address. The bypass requires no special knowledge or exploit tooling — any user who calls the router reaches the pool. The misconfiguration is a predictable consequence of the wrong-actor check.

---

### Recommendation

The extension must gate on the **economic actor** (the end user), not the intermediary. Two options:

**Option A — Pass the original user through the router (preferred):** Have the router forward the original `msg.sender` as a separate `sender` field in `extensionData`, and have the extension decode and check that value. This requires a coordinated change to the router and extension.

**Option B — Check `recipient` or require direct-pool-only swaps:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender != tx.origin` (fragile) or add an explicit check that the pool's `sender` is not a known router.

**Option C — Allowlist-aware router:** Add a `swapper` parameter to the router's swap functions that is forwarded to the pool as an override `sender` for extension purposes, validated against `msg.sender` inside the router.

The cleanest fix is Option A: the router stores `msg.sender` in transient storage (it already uses transient storage for callback context) and the extension reads it from a well-known slot.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only `alice` is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) swaps through the router
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1_000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// ← succeeds: extension checked allowedSwapper[pool][router] = true
//   bob's address was never evaluated
vm.stopPrank();
```

**Root cause trace:**
- [1](#0-0)  — pool passes `msg.sender` (router) as `sender` to `_beforeSwap`
- [2](#0-1)  — `_beforeSwap` forwards `sender` unchanged to the extension
- [3](#0-2)  — extension checks `allowedSwapper[pool][sender]` where `sender` is the router, not the user
- [4](#0-3)  — router calls `pool.swap(...)` as `msg.sender`, losing the original user identity

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
