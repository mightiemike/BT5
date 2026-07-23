Now I have all the code I need. Let me trace the exact call chain:

The call chain is fully traceable. Here is the analysis:

**Call chain:**

1. EOA → `MetricOmmSimpleRouter.exactInput` (line 92)
2. Router → `pool.swap(...)` — `msg.sender` of this call = **router address** [1](#0-0) 
3. `MetricOmmPool.swap` → `_beforeSwap(msg.sender, ...)` — passes **router** as `sender` [2](#0-1) 
4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]` [3](#0-2) 

If `allowedSwapper[pool][router] = true`, the check passes for **every EOA** that routes through the router, because the extension never sees the original caller — only the router address.

---

### Title
SwapAllowlistExtension Sender Identity Collapse via Router — Any EOA Bypasses Per-Pool Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap` call. When `MetricOmmSimpleRouter` is the caller, `sender` is always the router address, not the originating EOA. A pool admin who allowlists the router to permit router-based swaps inadvertently opens the allowlist to every EOA, because the router imposes no caller-level access control.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the router when called via exactInput
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the admin has set that entry to `true` (the natural configuration to permit router-based swaps), the gate passes unconditionally for any EOA that calls `exactInput` or `exactOutput` on the router, because `MetricOmmSimpleRouter` has no per-caller access control.

The original EOA identity is never forwarded to the extension. The `extensionDatas[i]` array is caller-supplied and unverified, so it cannot be used to reconstruct a trustworthy original-caller identity.

### Impact Explanation
Any EOA can execute swaps against pools that are intended to be restricted to a specific allowlist, by routing through `MetricOmmSimpleRouter`. The allowlist invariant — "only approved addresses may swap" — is broken for every pool that allowlists the router. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, or rate-limited pools), this enables unauthorized trading, potential front-running of restricted liquidity, and violation of the pool's access-control guarantees. This is a broken core access-control flow meeting the "admin-boundary break" impact gate.

### Likelihood Explanation
A pool admin who wants to allow router-based swaps for their allowlisted users has no other option than to add the router to the allowlist. This is the natural and expected configuration. The bypass is therefore triggered by a routine, non-malicious admin action, making it highly likely to occur in production deployments that use both `SwapAllowlistExtension` and `MetricOmmSimpleRouter`.

### Recommendation
The `SwapAllowlistExtension` must check the original caller, not the immediate `msg.sender` of `pool.swap`. Two approaches:

1. **Trusted forwarding via `extensionData`**: The router encodes `msg.sender` (the EOA) into `extensionData` for each hop, and the extension verifies it against a registry of trusted routers before trusting the forwarded address. This requires the extension to maintain a router whitelist and decode the payload.
2. **Direct-only allowlist**: Document clearly that `SwapAllowlistExtension` is incompatible with any intermediary router and must only be used when callers interact with the pool directly. Enforce this by checking that `sender == tx.origin` or by rejecting known router addresses as `sender`.

### Proof of Concept
```solidity
// Foundry integration test sketch
function test_allowlistBypass_viaRouter() public {
    // Setup: pool with SwapAllowlistExtension, router allowlisted, EOA not allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT in the allowlist

    // Attacker calls exactInput through the router
    vm.prank(attacker);
    router.exactInput(IMetricOmmSimpleRouter.ExactInputParams({
        tokens: [tokenIn, tokenOut],
        pools: [address(pool)],
        extensionDatas: [bytes("")],
        zeroForOneBitMap: 1,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1
    }));
    // Assert: swap succeeds despite attacker not being in the allowlist
    // The extension saw sender=router, which is allowlisted, so it passed
}
```

The swap succeeds because `SwapAllowlistExtension.beforeSwap` receives `sender = address(router)`, which is allowlisted, and never inspects the originating EOA. [4](#0-3) [2](#0-1) [5](#0-4)

### Citations

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
