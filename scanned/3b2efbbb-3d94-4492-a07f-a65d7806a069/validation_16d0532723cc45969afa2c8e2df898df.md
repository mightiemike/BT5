The call chain is fully traceable. Here is the analysis:

**Call chain:**
1. `attacker` → `router.exactInputSingle(params)`
2. Router → `pool.swap(recipient, ...)` — `msg.sender` at the pool is the **router**
3. Pool → `_beforeSwap(msg.sender=router, ...)` [1](#0-0) 
4. Pool → `extension.beforeSwap(sender=router, ...)` — `msg.sender` at the extension is the **pool** [2](#0-1) 
5. Extension checks: `allowedSwapper[pool][router]` — if `true`, swap proceeds regardless of who called the router [3](#0-2) 

The router never forwards the original `msg.sender` (the attacker's EOA) to the pool. It calls `pool.swap()` directly, making itself the `sender` the extension sees. [4](#0-3) 

The allowlist check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router]`, which is `true` by assumption — so the check passes for **every user of the router**, not just explicitly allowlisted EOAs. [5](#0-4) 

This is a confirmed vulnerability.

---

### Title
Swap allowlist bypassed via allowlisted router: any EOA can swap through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address, not the originating EOA. If the router is allowlisted, every user of the router bypasses the allowlist.

### Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. `MetricOmmPool.swap()` passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(msg.sender, recipient, ...);  // msg.sender = router
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without substituting the originating EOA:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(params.recipient, ...);
```

The `msg.sender` stored in transient storage is used only for the payment callback, not forwarded to the pool as the swap initiator. The pool sees the router as `sender`. If `allowedSwapper[pool][router] == true`, the check passes unconditionally for any EOA that calls the router.

### Impact Explanation
Pool curation via the swap allowlist is completely broken for any pool that allowlists the router. Non-allowlisted users can freely swap, draining LP positions and violating the core invariant that only explicitly permitted addresses may trade. This constitutes broken core pool functionality with direct fund impact on LPs.

### Likelihood Explanation
Allowlisting a public router is the natural and expected configuration for a pool that wants to permit router-based trading. Any pool admin who does so unknowingly opens the pool to all router users. The attacker path requires no special privileges — only calling a public router function.

### Recommendation
The extension must verify the **originating** user, not the immediate `pool.swap()` caller. Options:
- Have the router pass the originating EOA through `extensionData`, and have the extension decode and check it (requires trust that the router sets it correctly, or a registry of trusted routers).
- Alternatively, the allowlist should check `recipient` or require the pool to expose the original initiator through a separate mechanism.
- A cleaner fix: do not allowlist routers; instead allowlist individual EOAs only, and document that router allowlisting defeats the guard.

### Proof of Concept
```solidity
// Foundry integration test sketch
function test_allowlistBypass() public {
    // Setup: pool with SwapAllowlistExtension, router allowlisted, attacker EOA not allowlisted
    swapAllowlist.setAllowedToSwap(pool, address(router), true);
    // allowedSwapper[pool][attacker] == false

    // Attacker calls router — router calls pool.swap(sender=router)
    // Extension sees allowedSwapper[pool][router] == true → passes
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        tokenIn: token0,
        extensionData: ""
    }));
    // Assert: swap succeeded despite attacker not being allowlisted
}
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
