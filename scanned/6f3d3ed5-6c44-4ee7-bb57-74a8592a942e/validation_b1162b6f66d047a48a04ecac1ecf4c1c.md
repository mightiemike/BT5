The call path is confirmed. Let me trace it precisely through the code.

**Call path:**
1. `trader → MetricOmmSimpleRouter.exactInputSingle()` — `msg.sender` = trader
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, ...)` — `msg.sender` to pool = **router**
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — passes **router** as `sender`
4. `ExtensionCalling._beforeSwap()` encodes `sender = router` and calls the extension
5. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

The check passes if the router is allowlisted — the originating trader is never examined.

---

### Title
Swap Allowlist Bypassed via Router: `sender` Is Router, Not Originating Trader — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the originating EOA. Any non-allowlisted trader can therefore bypass the allowlist by routing through the router, provided the router itself is allowlisted.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()`, the router is `msg.sender` to the pool: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, never `allowedSwapper[pool][trader]`. A pool admin who allowlists the router (a natural configuration — e.g., to permit their own router-mediated trading) inadvertently opens the pool to every user of that public router.

### Impact Explanation
Any non-allowlisted EOA can execute swaps on a restricted pool and receive output tokens, fully defeating the access-control invariant of `SwapAllowlistExtension`. Pools restricted for compliance, KYC, or whitelist-only LP reasons are silently open to the public via the router.

### Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap interface. Pool admins who configure a swap allowlist and also want to support router-based trading will naturally allowlist the router address. The bypass requires no privileged action beyond that ordinary configuration.

### Recommendation
`SwapAllowlistExtension` cannot recover the originating trader from the `sender` argument alone when a router is involved. Two sound fixes:

1. **Pass the originating payer through `extensionData`**: the router encodes `msg.sender` (the trader) into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Require direct pool interaction for allowlisted pools**: document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the extension or pool level (e.g., revert if `sender` is a known router).

### Proof of Concept
```solidity
// Foundry integration test sketch
function test_allowlistBypass_viaRouter() public {
    // Pool admin allowlists only the router
    swapAllowlist.setAllowedToSwap(pool, address(router), true);
    // allowedSwapper[pool][trader] = false (default)

    // Non-allowlisted EOA calls the router
    vm.startPrank(trader);
    token0.approve(address(router), type(uint256).max);
    uint256 out = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: pool,
            recipient: trader,
            tokenIn: address(token0),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            deadline: block.timestamp,
            extensionData: ""
        })
    );
    vm.stopPrank();

    // Swap succeeds; trader receives output despite not being allowlisted
    assertGt(out, 0);
    assertFalse(swapAllowlist.isAllowedToSwap(pool, trader));
}
```

The swap succeeds because `beforeSwap` receives `sender = router` (allowlisted) rather than `sender = trader` (not allowlisted).

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
