### Title
`SwapAllowlistExtension` gates the direct pool caller (`sender`) instead of the end user, enabling any user to bypass the swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `sender` = router address, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their allowed users inadvertently opens the allowlist to all users, because the router is a shared public contract.

### Finding Description

In `MetricOmmPool.swap()`, `sender` is set to `msg.sender` — the direct caller of the pool: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` is called, the router calls `pool.swap(...)` directly, making itself `msg.sender` to the pool: [4](#0-3) 

So `sender` seen by the extension = **router address**, not the end user. The extension has no visibility into the end user's identity.

This creates a dual-identity problem directly analogous to the external bug (Ownable vs. AccessControl divergence):

| External Bug | Metric OMM Analog |
|---|---|
| Owner ≠ DEFAULT_ADMIN_ROLE holder after transfer | End user ≠ direct pool caller when router is used |
| Owner can't manage roles; admin can't upgrade | Admin can't enable router UX without opening allowlist to everyone |

The pool admin faces an impossible choice:
1. **Don't allowlist the router** → allowed users can't use `MetricOmmSimpleRouter` (broken UX)
2. **Allowlist the router** → all users bypass the allowlist by routing through it (broken security)

The `setAllowedToSwap` admin setter gates by swapper address: [5](#0-4) 

There is no mechanism to distinguish "router called on behalf of Alice" from "router called on behalf of Eve." The router is a shared public contract — allowlisting it is equivalent to `setAllowAllSwappers(pool, true)`.

### Impact Explanation

**High.** A curated pool's swap allowlist is completely bypassed by any user routing through `MetricOmmSimpleRouter`. Unauthorized users can trade on a pool designed for specific participants (e.g., KYC'd users, institutional counterparties), violating the pool's intended access control policy. LP funds are directly at risk if the pool's market dynamics or fee assumptions depend on controlled participation — unauthorized swappers can extract value from LPs at oracle-anchored prices without the pool admin's consent.

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to allowlist the router address. This is a natural and expected admin action for any curated pool that wants to support router-mediated swaps for their allowed users. The admin has no way to achieve both goals simultaneously with the current design, making this a predictable operational mistake. No existing test covers the router-mediated bypass path.

### Recommendation

Gate the allowlist on the end user's identity rather than the direct pool caller. The cleanest fix is to have the router forward the end user's address in `extensionData`, and have `SwapAllowlistExtension` decode and verify it:

```solidity
function beforeSwap(
    address sender,
    address,
    bool, int128, uint128, uint256, uint128, uint128,
    bytes calldata extensionData
) external view override returns (bytes4) {
    // If extensionData encodes an end-user address, gate on that; else gate on sender.
    address swapper = (extensionData.length >= 32)
        ? abi.decode(extensionData, (address))
        : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][swapper]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The router would then encode `msg.sender` into `extensionData` before calling the pool. Alternatively, document explicitly that allowlisting the router address is equivalent to opening the allowlist to all users, so pool admins can make an informed decision.

### Proof of Concept

```solidity
function test_SwapAllowlistBypassViaRouter() public {
    // Pool admin allowlists Alice and the router
    // (reasonable: admin wants Alice to be able to use the router)
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Eve (not allowlisted) bypasses the allowlist via the router
    vm.prank(eve);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        tokenIn:          address(token0),
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1_000,
        amountOutMinimum: 0,
        recipient:        eve,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    }));
    // Eve successfully swaps despite not being allowlisted.
    // The extension saw sender = router (allowlisted), not Eve (not allowlisted).
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
