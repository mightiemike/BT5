### Title
`SwapAllowlistExtension` gates the router address instead of the original user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `sender` equals the **router address**, not the original user. If the pool admin allowlists the router to support router-mediated swaps, every non-allowlisted user can bypass the per-user gate by routing through the router.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender`.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`_beforeSwap` forwards that value unchanged as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — Extension checks `sender`, which is the router when the router calls the pool.**

`SwapAllowlistExtension.beforeSwap()` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = pool (correct), and `sender` = whoever called `pool.swap()`. When the router is the caller, `sender` = router address.

**Step 3 — The router calls `pool.swap()` directly, with no mechanism to forward the original user.**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` as `msg.sender = router`: [4](#0-3) 

The original user's address is stored only in transient callback context for payment settlement; it is never forwarded to the extension as `sender`. The `extensionData` bytes are user-controlled and not authenticated, so the extension cannot safely read the original user from them.

**Step 4 — The forced admin dilemma.**

For a curated pool to support router-based swaps at all, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted. The per-user gate is completely neutralised for the router path. [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps at oracle-anchored prices against LP capital that was deposited under the assumption that only vetted counterparties could trade. This is a direct, fund-impacting bypass of a configured security control: LP principal is exposed to unrestricted oracle-price extraction by any address.

---

### Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is the natural and expected configuration for any curated pool that also wants to support the standard periphery router — the admin has no other way to enable router-based swaps for the allowlisted users. The condition is therefore likely to be met in production deployments that combine `SwapAllowlistExtension` with `MetricOmmSimpleRouter` support.

---

### Recommendation

The extension must be able to identify the **original initiating user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Authenticated `extensionData` field**: The router signs or encodes the original `msg.sender` into `extensionData`, and the extension verifies it against a trusted router registry. This requires the extension to know which routers are trusted.

2. **Transient initiator slot on the pool**: The pool stores the original `msg.sender` in a transient slot before any extension call, and extensions read it via a pool view function. This is the cleanest approach and mirrors how the router already stores its own callback context.

Until fixed, pool admins should not allowlist the router address on pools using `SwapAllowlistExtension`; router-based swaps must be disabled for curated pools.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    (necessary to allow any router-based swap for allowlisted users)
  alice is NOT individually allowlisted

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)          [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
  → swap executes; alice trades on the curated pool without being allowlisted

Result:
  allowedSwapper[pool][alice] == false, yet alice's swap settles at oracle price
  against LP capital that was deposited under a curated-counterparty assumption.
``` [3](#0-2) [1](#0-0) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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
