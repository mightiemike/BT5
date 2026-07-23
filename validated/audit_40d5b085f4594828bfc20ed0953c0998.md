The vulnerability is real and confirmed. Let me trace the exact call path:

**Step 1:** `MetricOmmSimpleRouter.exactInputSingle()` calls the pool's `swap()` with `msg.sender = router`: [1](#0-0) 

**Step 2:** `MetricOmmPool.swap()` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is the router, not the originating EOA: [3](#0-2) 

The allowlist gates the immediate caller of `pool.swap()`, not the economic actor. When the router is allowlisted, every EOA using the router bypasses per-trader curation entirely.

---

### Title
SwapAllowlistExtension.beforeSwap checks router address instead of originating trader, allowing any EOA to bypass per-trader swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that address is the router contract, not the originating EOA. If the pool admin allowlists the router (a natural operational choice for a curated pool that still wants to support standard routing UX), every user of the router can swap regardless of individual allowlist status.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230
_beforeSwap(msg.sender, recipient, ...);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle()`, `sender = address(router)`. The check becomes `allowedSwapper[pool][router]`, completely ignoring the originating EOA.

A pool admin who allowlists the router to permit standard routing UX inadvertently opens the pool to all router users. Conversely, a pool admin who allowlists specific EOAs will find those EOAs cannot swap through the router at all (since the router is not allowlisted), breaking the intended UX.

### Impact Explanation
The swap allowlist — the primary mechanism for pool curation — is rendered ineffective for any pool that allowlists the router. Any non-allowlisted EOA can call `router.exactInputSingle()` and successfully swap on a pool that was intended to be restricted. LPs who deposited into a curated pool expecting only vetted counterparties are exposed to unrestricted trading, which can cause direct fund loss through adverse selection or violation of regulatory/compliance constraints the pool was designed to enforce.

### Likelihood Explanation
The router is a standard, publicly deployed periphery contract. Pool admins operating curated pools with allowlists will naturally want to support the router for UX reasons and will allowlist it. The bypass requires no special privileges — any EOA with tokens can call `router.exactInputSingle()`.

### Recommendation
Pass the originating user through the extension rather than the immediate `msg.sender`. One approach: add an `originator` field to the swap call or extension data that the router populates with `msg.sender` before calling the pool. The extension can then verify `allowedSwapper[pool][originator]`. Alternatively, document clearly that allowlisting the router grants access to all router users, and provide a separate per-user gating mechanism that the router populates.

### Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
// 2. Pool admin allowlists only the router:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 3. Non-allowlisted EOA calls the router:
vm.prank(nonAllowlistedEOA);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: nonAllowlistedEOA,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));

// 4. Swap succeeds — allowlist check passed allowedSwapper[pool][router] = true
// 5. Assert LP token1 balance decreased (pool traded with non-allowlisted user)
assertLt(token1.balanceOf(address(pool)), token1BalanceBefore);
```

### Citations

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
