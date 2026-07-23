The call chain is fully traceable. Here is the exact flow:

**Step 1 — Router call:**
`exactInputSingle` stores `msg.sender` (EOA) as the payer in transient storage, then calls `pool.swap(params.recipient, ...)` — the router is now `msg.sender` to the pool. [1](#0-0) 

**Step 2 — Pool passes `msg.sender` (router) as `sender` to `_beforeSwap`:** [2](#0-1) 

**Step 3 — Extension checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`:** [3](#0-2) 

The EOA's address is **never consulted**. The guard passes because `allowedSwapper[pool][router] == true`, regardless of whether `allowedSwapper[pool][EOA]` is false.

---

### Title
`SwapAllowlistExtension` checks the router address instead of the originating EOA, allowing any non-allowlisted swapper to bypass the guard via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps using `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating EOA. Any non-allowlisted EOA can therefore bypass the guard by routing through the router, provided the router itself is allowlisted.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [4](#0-3) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [5](#0-4) 

`SwapAllowlistExtension.beforeSwap` then checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
    revert NotAllowedToSwap();
``` [3](#0-2) 

When `exactInputSingle` is called, the router is `msg.sender` to the pool, so `sender` = router address. The originating EOA is stored only in transient callback context (for payment), never surfaced to the extension: [6](#0-5) 

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses is fully bypassed for any EOA that routes through `MetricOmmSimpleRouter`. The allowlist extension's core invariant — "only allowlisted addresses may swap" — is broken. Non-allowlisted parties can execute swaps in pools that are intended to be permissioned, causing direct fund movement (token exchange) that the pool operator explicitly prohibited. This constitutes broken core pool functionality and unauthorized swap execution.

### Likelihood Explanation
The router is a public, permissionless contract. Any EOA can call `exactInputSingle`. The only precondition is that the pool admin has allowlisted the router address — a natural configuration for any pool that intends to support router-based swaps while also restricting the set of allowed swappers. The two goals are mutually exclusive under the current design, but a pool admin has no way to know this from the extension's interface or documentation.

### Recommendation
The `sender` argument passed to `beforeSwap` must represent the originating economic actor, not the intermediate contract. Options:

1. **Router-side**: Have `MetricOmmSimpleRouter` pass the originating EOA (`msg.sender`) as a dedicated field in `extensionData`, and have `SwapAllowlistExtension` decode and check it when present.
2. **Extension-side**: Document explicitly that `SwapAllowlistExtension` gates the direct caller of `pool.swap`, not the EOA, and that allowlisting the router opens the pool to all router users.
3. **Pool-side**: Add a first-class `originator` parameter to `pool.swap` that the router populates with `msg.sender`, and pass it through to extensions.

### Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists only the router:
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// EOA is NOT allowlisted:
// swapExtension.allowedSwapper(pool, EOA) == false

// 3. Non-allowlisted EOA calls router — swap succeeds (should revert)
vm.prank(nonAllowlistedEOA);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
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
// Swap succeeds: allowedSwapper[pool][router] == true is checked,
// allowedSwapper[pool][nonAllowlistedEOA] == false is never checked.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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
```
