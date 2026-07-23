### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of that call is the router, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted, every user on the internet can bypass the curation gate; if the router is not allowlisted, every individually-allowlisted user is silently locked out of the primary swap interface.

---

### Finding Description

**Pool passes `msg.sender` as `sender` to every extension hook.**

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), NOT the end user
    recipient,
    ...
);
``` [1](#0-0) 

**`SwapAllowlistExtension` checks that `sender` argument.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

**`MetricOmmSimpleRouter` calls `pool.swap()` directly; the pool's `msg.sender` is the router.**

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops): [4](#0-3) [5](#0-4) 

In every case the extension receives `sender = router_address`, so the lookup is:

```
allowedSwapper[pool][router]   ← checked
allowedSwapper[pool][user]     ← never checked
```

---

### Impact Explanation

**Bypass path (router is allowlisted):** A pool admin who allowlists the router as a "trusted intermediary" inadvertently opens the gate to every user on the internet. Any address can call `router.exactInputSingle(...)` and the extension will pass because `allowedSwapper[pool][router] == true`. The entire curation invariant collapses.

**Lockout path (router is not allowlisted):** Individually allowlisted users who attempt to swap through the router are rejected because `allowedSwapper[pool][router] == false`. The primary user-facing swap interface is unusable for the pool's intended participants, breaking core swap functionality.

Both outcomes are fund-impacting: the bypass path lets unauthorized counterparties trade against LP positions on curated pools; the lockout path makes LP assets inaccessible to legitimate swappers.

---

### Likelihood Explanation

- The router is the canonical, documented swap interface for end users.
- No special setup is required; any user can call `router.exactInputSingle` or `router.exactInput`.
- The pool admin has no on-chain signal that the allowlist is being evaluated against the router rather than the user.
- The unit tests for `SwapAllowlistExtension` call the extension directly with `vm.prank(address(pool))` and pass the swapper address explicitly — they never exercise the router path, so the mismatch is untested. [6](#0-5) 

---

### Recommendation

The pool should pass the economically relevant actor — the end user — as `sender`, not the immediate `msg.sender`. One standard approach is to let the caller supply a `sender` override that the pool validates (e.g., via a signed permit or a trusted-forwarder pattern). Alternatively, `SwapAllowlistExtension` should gate on `recipient` when the caller is a known router, or the router should forward the original `msg.sender` through `extensionData` and the extension should decode and verify it.

At minimum, the `SwapAllowlistExtension` NatSpec and the router documentation must warn that the allowlist gates the direct `pool.swap()` caller, not the end user, so pool admins can make an informed configuration decision.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in the `beforeSwap` slot.
2. Call `extension.setAllowedToSwap(pool, router, true)` — allowlist the router (a realistic admin action).
3. As a non-allowlisted EOA (`attacker`), call `router.exactInputSingle({pool: pool, recipient: attacker, ...})`.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`. The extension checks `allowedSwapper[pool][router] == true` → passes.
5. The swap executes. `attacker` receives tokens from a pool that was supposed to be closed to them.

Alternatively, to demonstrate the lockout:

1. Same setup but do **not** allowlist the router; instead allowlist `alice` directly.
2. `alice` calls `router.exactInputSingle(...)`.
3. The extension checks `allowedSwapper[pool][router] == false` → `NotAllowedToSwap` revert.
4. `alice` must call `pool.swap()` directly (no slippage protection, no multi-hop support) or cannot swap at all. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-41)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L32-38)
```text
  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
