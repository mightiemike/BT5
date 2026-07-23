### Title
SwapAllowlistExtension Bypassed via MetricOmmSimpleRouter — Any User Can Swap on Allowlist-Restricted Pools - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the identity of the **immediate caller of the pool's `swap` function** (`sender`), not the original end-user. When `MetricOmmSimpleRouter` is used, the pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`. If the pool admin allowlists the router address (a natural step to enable router-mediated swaps), every user — including non-allowlisted ones — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Root cause — identity mismatch in the hook call chain:**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
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

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol lines 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**Attack path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` wired into `beforeSwap` to restrict trading to a curated set of addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — a natural action to let allowlisted users reach the pool through the standard periphery router.
3. A non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle(...)` or `exactInput(...)`.
4. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The original user's identity is never checked; the allowlist is fully bypassed.

`MetricOmmSimpleRouter.exactInputSingle` (lines 71–86) passes no original-caller context to the pool — only `params.recipient` and the router's own address as `msg.sender`.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade against a pool's LP liquidity. Once the router is allowlisted, the guard is inoperative for all router-mediated swaps. LPs who deployed capital into a restricted pool (e.g., to serve only KYC'd counterparties, or to avoid informed-trader flow) are exposed to unrestricted swap volume from any address. This constitutes broken core pool functionality with potential direct loss of LP assets through unintended adverse selection.

---

### Likelihood Explanation

Allowlisting the router is the expected operational step for any pool that wants to support both an allowlist and the standard periphery. The pool admin has no in-protocol warning that doing so nullifies per-user restrictions. The bypass is reachable by any public user with no special privileges, no malicious setup, and no non-standard tokens.

---

### Recommendation

The extension must verify the **original end-user**, not the intermediate router. Two viable approaches:

1. **Router forwards caller identity in `extensionData`**: `MetricOmmSimpleRouter` encodes `msg.sender` into the `extensionData` bytes it passes to the pool; `SwapAllowlistExtension` decodes and checks that address when `sender` is a known router.
2. **Pool passes original payer via callback context**: The pool could expose the payer stored in transient callback context so extensions can read it, though this couples core and periphery.

Additionally, document clearly that allowlisting a router address opens the pool to all router callers, not just individually allowlisted users.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension wired to beforeSwap.
2. Pool admin: extension.setAllowedToSwap(pool, alice, true)
              extension.setAllowedToSwap(pool, router, true)   // intended to let alice use router
3. Bob (not allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: bob, ...})
4. Pool calls extension.beforeSwap(sender=router, ...)
   → allowedSwapper[pool][router] == true → passes
5. Bob's swap executes against LP liquidity despite not being on the allowlist.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
