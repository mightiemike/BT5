### Title
SwapAllowlistExtension Gates the Router Address Instead of the Economic Actor, Enabling Full Allowlist Bypass Through the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which resolves to the router's address when a swap is routed through `MetricOmmSimpleRouter`. A pool admin who allowlists the router address (a natural action to enable router-based swaps for their allowlisted users) inadvertently opens the gate to every user, defeating the curated-pool invariant entirely.

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to every before-swap hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router contract, so `sender = address(router)`: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Contrast with the deposit path.** `DepositAllowlistExtension.beforeAddLiquidity` deliberately ignores `sender` and checks `owner` — the economic beneficiary — which the pool passes as a separate, explicit argument: [5](#0-4) 

No equivalent "economic actor" argument exists on the swap path; the only identity the extension can observe is the technical caller (`sender`).

### Impact Explanation

Two distinct fund-impacting outcomes follow from this wrong-actor binding:

1. **Allowlist bypass (High).** A pool admin who allowlists the router address — a natural step to let their curated users trade via the standard periphery — simultaneously grants swap access to every address on the network. Any non-allowlisted user calls `router.exactInputSingle()`, the extension sees `allowedSwapper[pool][router] = true`, and the swap executes. The curated-pool invariant ("only approved counterparties may trade") is silently broken, exposing LP funds to unrestricted arbitrage or extraction.

2. **Broken swap flow for allowlisted users (Medium).** Allowlisted users whose addresses are registered in `allowedSwapper` cannot use the router at all, because the extension sees the router's address and reverts. They are forced to call `pool.swap()` directly, losing access to multi-hop routing, slippage protection, and deadline enforcement provided by the periphery.

### Likelihood Explanation

The bypass is triggered by a single, reasonable admin action: allowlisting the router so that approved users can trade through the standard periphery. The admin has no on-chain signal that this also opens the pool to everyone. The broken-flow impact (point 2) requires no admin mistake at all — it is present for every allowlisted user the moment they attempt a router swap.

### Recommendation

The swap allowlist must gate the economic actor, not the technical caller. Two viable approaches:

- **Encode the originating user in `extensionData`**: The router appends `abi.encode(msg.sender)` to `extensionData` before calling `pool.swap()`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address. The extension must also verify `msg.sender` (the pool) is a known factory pool to prevent spoofing.
- **Introduce a `recipient`-based check**: If the intended policy is "only approved recipients may receive output," check the `recipient` argument instead of `sender`. This matches the deposit extension's pattern of checking the economic beneficiary.

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension as the beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, user1, true) and setAllowedToSwap(pool, user2, true).
3. Admin calls setAllowedToSwap(pool, address(router), true)
   — intending to let user1/user2 trade via the router.
4. user3 (not in the allowlist) calls:
       router.exactInputSingle({pool: pool, ..., recipient: user3})
5. Router calls pool.swap(user3, ...) with msg.sender = router.
6. Pool calls extension.beforeSwap(router, user3, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true  → no revert.
8. Swap executes for user3, who was never allowlisted.
   LP funds are now accessible to any address that routes through the router.
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
