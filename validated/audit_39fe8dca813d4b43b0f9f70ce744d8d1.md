### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual user, permanently blocking all EOA swaps or fully bypassing the allowlist — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — which is `msg.sender` of `pool.swap()`, i.e., the router contract — against the per-pool allowlist. Because EOAs cannot implement `IMetricOmmSwapCallback` and must route through `MetricOmmSimpleRouter`, the allowlist either permanently blocks all EOA swaps (if the router is not allowlisted) or is completely bypassed for every user (if the router is allowlisted). There is no configuration that simultaneously enforces per-user gating and supports the standard router path.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` of `pool.swap()`: [4](#0-3) 

So the allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual end user's address is never consulted.

Because `pool.swap()` requires the caller to implement `IMetricOmmSwapCallback` (the pool calls `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` to settle), EOAs cannot call the pool directly — they must use the router. This makes the router the only viable swap entry point for ordinary users. [5](#0-4) 

Contrast this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks `owner` (the LP position owner) rather than `sender` (the adder contract), preserving per-user semantics even when the `MetricOmmPoolLiquidityAdder` is the caller: [6](#0-5) 

The swap extension has no equivalent design — it checks `sender` (the router) rather than the economically relevant actor.

---

### Impact Explanation

Two mutually exclusive failure modes arise, both fund-impacting:

**Mode A — Permanent swap lock (direct Fantom analog):**
Pool admin allowlists specific user addresses via `setAllowedToSwap(pool, user, true)` but does not add the router. Every EOA swap through `MetricOmmSimpleRouter` reverts with `NotAllowedToSwap` because `allowedSwapper[pool][router]` is `false`. Since EOAs cannot implement the swap callback, they have no alternative path. The pool's swap functionality is permanently broken for all EOA users, trapping LP principal in a pool that cannot be traded against.

**Mode B — Full allowlist bypass:**
To unblock users, the pool admin adds the router: `setAllowedToSwap(pool, router, true)`. Now `allowedSwapper[pool][router]` is `true`, so the check passes for every caller regardless of their identity. Any address — including those the admin explicitly never allowlisted — can swap through the router. The allowlist guard is completely nullified, and the admin-boundary invariant is broken.

There is no configuration that avoids both failure modes simultaneously.

---

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` with per-user allowlisting (i.e., `allowAllSwappers = false`) and expects users to swap through `MetricOmmSimpleRouter` will hit Mode A immediately on the first router-mediated swap. The pool admin's only remediation option (adding the router) triggers Mode B. The trigger requires no special privilege — any allowlisted EOA attempting a normal swap is sufficient.

---

### Recommendation

Mirror the `DepositAllowlistExtension` pattern: gate the economically relevant actor, not the intermediary. For swaps, the relevant actor is the end user who initiated the transaction. One approach is to pass the original initiator through `extensionData` (set by the router) and verify it in the extension. Alternatively, the extension could check `recipient` when `sender` is a known router, or the pool could expose the original initiator via a transient-storage accessor analogous to `inSwap()`.

A minimal fix in `SwapAllowlistExtension.beforeSwap` would be to decode the true initiator from `extensionData` when `sender` is a recognized router, and fall back to checking `sender` directly for non-router callers.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is allowlisted.
3. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. Router calls `pool.swap(recipient=alice, ...)` — `msg.sender` of `pool.swap()` = router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `false`.
7. Extension reverts `NotAllowedToSwap`.
8. Alice's swap fails despite being explicitly allowlisted.

To demonstrate Mode B:
- Pool admin calls `setAllowedToSwap(pool, router, true)`.
- Bob (never allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
- Extension evaluates `allowedSwapper[pool][router]` → `true`.
- Bob's swap succeeds — the allowlist is bypassed.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L257-263)
```text
      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
