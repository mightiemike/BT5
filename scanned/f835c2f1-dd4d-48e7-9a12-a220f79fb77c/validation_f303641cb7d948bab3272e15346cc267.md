I now have all the information needed to write the finding. Let me confirm the exact call chain one more time.

### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` from the pool's perspective — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool that allowlists the router (required for router-mediated swaps to function at all) exposes its swap allowlist to complete bypass by any unprivileged user.

---

### Finding Description

The call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._callExtensionsInOrder(...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct for the per-pool mapping key) and `sender` is the router address — not the end-user. The extension is checking whether the **router** is allowlisted, not whether the **user** is allowlisted.

This creates an inescapable dilemma for any pool that uses `SwapAllowlistExtension` and also wants to support the standard periphery router:

| Router allowlisted? | Effect |
|---|---|
| No | All router-mediated swaps revert, even for allowlisted users |
| Yes | **Any** user can bypass the allowlist by routing through the router |

The second row is the exploitable path: the pool admin must allowlist the router to make the pool usable via the standard periphery, and doing so silently opens the allowlist to every address on-chain.

---

### Impact Explanation

**Direct loss of curation / policy enforcement.** `SwapAllowlistExtension` is the mechanism by which a pool admin restricts trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled accounts). Once the router is allowlisted, any unprivileged user can execute swaps against the pool's liquidity at oracle-derived prices, draining LP value through toxic flow that the allowlist was designed to prevent. This is a broken core pool functionality with direct LP principal loss potential, satisfying the Sherlock High threshold.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps. Any pool operator who deploys a `SwapAllowlistExtension`-gated pool and then allowlists the router (the only way to make the pool accessible via the standard UI/SDK) immediately enables the bypass. No special privileges, flash loans, or exotic token behavior are required — a plain `exactInputSingle` call from any EOA suffices.

---

### Recommendation

The extension must check the **economically relevant actor** — the end-user — not the immediate pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires the extension to trust the encoding, which reintroduces a trust assumption on the router.

2. **Check `recipient` instead of `sender` for router flows, or redesign the hook signature**: The cleanest fix is to have the pool pass the original initiator rather than `msg.sender`. Since the pool cannot know the original user when called by a router, the router should pass the user address explicitly in `extensionData`, and the extension should decode and gate on that value — with the pool verifying the router is a trusted forwarder.

3. **Simplest safe fix**: Remove the router from the allowlist and require users to call `pool.swap()` directly when the pool is allowlist-gated. Document this constraint explicitly so pool admins do not inadvertently allowlist the router.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin allowlists the router so that alice can use the standard UI.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// alice is NOT separately allowlisted — only the router is.

// Eve (not allowlisted) calls the router directly:
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: eve,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// pool.swap() is called with msg.sender = router.
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
// Eve's swap executes against the curated pool's liquidity.
```

The `beforeSwap` guard passes because `sender = address(router)` is allowlisted, regardless of who called the router. [4](#0-3) [5](#0-4) [6](#0-5)

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
