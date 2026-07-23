## Full Trace

### Call chain for a direct swap

```
user → pool.swap()
  msg.sender = user
  _beforeSwap(msg.sender=user, ...)
    extension.beforeSwap(sender=user, ...)
      msg.sender inside hook = pool
      checks: allowedSwapper[pool][user]   ✓ correct identity
```

### Call chain for a router-mediated swap

```
user → router.exactInputSingle(...)
  router → pool.swap()
    msg.sender = router
    _beforeSwap(msg.sender=router, ...)
      extension.beforeSwap(sender=router, ...)
        msg.sender inside hook = pool
        checks: allowedSwapper[pool][router]  ✗ wrong identity
```

The pool passes `msg.sender` of `pool.swap()` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension: [2](#0-1) 

The hook then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user: [3](#0-2) 

The router calls `pool.swap()` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

---

## Paused-pool angle: does not hold

The question's framing about "paused pool / LP withdrawals" is a dead end. `swap` carries `whenNotPaused`, so it reverts before `_beforeSwap` is ever reached when the pool is paused: [5](#0-4) 

`removeLiquidity` has no `whenNotPaused` guard and calls `_beforeRemoveLiquidity` / `_afterRemoveLiquidity`, never `_beforeSwap`: [6](#0-5) 

There is no code path that reaches `SwapAllowlistExtension::beforeSwap` through `removeLiquidity` under any pause state.

---

## The real issue: router identity substitution

The router identity substitution is a genuine design flaw with two symmetric consequences:

| Router allowlisted? | Effect |
|---|---|
| **Yes** | Any user can swap through the router; per-user allowlist is fully bypassed |
| **No** | Allowlisted users cannot use the standard router at all |

The pool admin has no way to simultaneously (a) allow allowlisted users to use the router and (b) block non-allowlisted users from doing the same, because the router collapses all user identities into one address.

---

## Verdict

### Title
Router address substitutes for actual swapper in `SwapAllowlistExtension::beforeSwap`, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
When a swap is routed through `MetricOmmSimpleRouter`, the pool receives the router as `msg.sender` and passes it as `sender` to `beforeSwap`. The hook gates on `allowedSwapper[pool][sender]`, so it checks the router's allowlist entry, not the end user's. A pool admin who allowlists the router to permit router-mediated swaps inadvertently opens the gate to every user.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as the `sender` argument to `_beforeSwap` and onward to every configured extension. `SwapAllowlistExtension.beforeSwap` uses that value as the swapper identity key. When the router intermediates the call, `sender` is the router address. The admin cannot distinguish individual users behind the router; allowlisting the router is equivalent to `allowAllSwappers = true` for all router users.

### Impact Explanation
Broken core access-control functionality. The allowlist is the sole mechanism for restricting who may swap on a gated pool. Router mediation silently voids per-user restrictions, allowing any unprivileged user to trade on a pool that was intended to be restricted.

### Likelihood Explanation
Medium. Requires a pool configured with `SwapAllowlistExtension` and the router allowlisted. Both conditions are expected in normal production use of the periphery.

### Recommendation
Pass the original end-user address through the router (e.g., via `extensionData` or a dedicated field) and have the extension read it, or document that the router is incompatible with `SwapAllowlistExtension` and enforce this at the factory level.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension`; set `allowedSwapper[pool][router] = true`, leave all other addresses blocked.
2. Call `router.exactInputSingle(...)` as any non-allowlisted EOA.
3. The hook receives `sender = router`, finds it allowlisted, and returns success — the swap executes despite the caller never being individually approved.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

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
