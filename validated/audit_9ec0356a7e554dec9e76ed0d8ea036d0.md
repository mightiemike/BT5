The call chain is fully traceable. Here is the exact flow and the precise point of failure:

**Call chain:**
1. Attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` — `msg.sender` = attacker
2. Router calls `IMetricOmmPoolActions(params.pool).swap(...)` — `msg.sender` to the pool = **router**
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — passes `msg.sender` = **router** as `sender`
4. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` is invoked
5. The guard checks: `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]` [1](#0-0) [2](#0-1) [3](#0-2) 

The `sender` argument received by `beforeSwap` is always `msg.sender` from the pool's perspective — the **direct caller of `pool.swap`**, which is the router, not the originating user. The extension has no access to the actual end-user address.

---

### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender = msg.sender` from the pool, which is the router when swaps are routed through `MetricOmmSimpleRouter`. The extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router to enable router-based swaps inadvertently grants swap access to every user who calls through the router, regardless of their individual allowlist status.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [4](#0-3) 

`_beforeSwap` forwards this value unchanged to the extension: [5](#0-4) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [6](#0-5) 

When `MetricOmmSimpleRouter.exactInputSingle` is used, the router is the direct caller of `pool.swap`: [7](#0-6) 

There is no mechanism in the extension call path to recover the original `tx.origin` or the user who called the router. The extension only ever sees the intermediary.

### Impact Explanation
A pool configured with `SwapAllowlistExtension` and `allowAllSwappers=false` intends to restrict swaps to a specific set of addresses. If the pool admin allowlists the router (a natural action to enable router-based trading), every user who calls through the router bypasses the per-user restriction. Non-allowlisted users can drain pool token1 (or token0) by routing swaps through `MetricOmmSimpleRouter`, violating the pool's access control invariant and causing direct loss of pool assets to unauthorized parties.

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants to support router-based swaps will allowlist the router. This is the expected operational pattern, making the bypass condition highly likely in practice.

### Recommendation
The `beforeSwap` extension interface should be extended to carry the originating user (e.g., via `extensionData` or a dedicated `originatingUser` parameter), or `SwapAllowlistExtension` should document explicitly that it cannot gate individual users when an intermediary router is used and that the router address must not be allowlisted if per-user restriction is intended. Alternatively, the router could pass the originating user address through `extensionData` and the extension could decode it, though this requires a trusted encoding convention.

### Proof of Concept
```solidity
// Pool setup:
// - SwapAllowlistExtension with allowAllSwappers=false
// - allowedSwapper[pool][router] = true   (router is allowlisted)
// - allowedSwapper[pool][attacker] = false (attacker is NOT allowlisted)

// Attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: POOL_WITH_SWAP_ALLOWLIST_EXTENSION,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: LARGE_AMOUNT,
    amountOutMinimum: 0,
    recipient: attacker,
    ...
}));

// Inside pool.swap: msg.sender = router
// _beforeSwap(sender=router, ...)
// SwapAllowlistExtension checks: allowedSwapper[pool][router] = true → PASSES
// Swap executes, attacker receives token1 despite not being individually allowlisted
assert(token1.balanceOf(attacker) > 0); // succeeds
```

### Citations

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
