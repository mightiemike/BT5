### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. If the pool admin adds the router to the allowlist to support router-based swaps (the natural production configuration), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description

**Actor binding in the pool:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards it verbatim to the extension: [2](#0-1) 

**What the router passes as `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router itself the `msg.sender` at the pool: [3](#0-2) 

**What the extension checks:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router address — not the end user: [4](#0-3) 

**Contrast with `DepositAllowlistExtension`:**

The deposit extension correctly checks `owner` (the explicit position owner passed by the liquidity adder as the actual user), not `sender` (the adder contract): [5](#0-4) 

This asymmetry confirms the swap extension is binding to the wrong actor.

### Impact Explanation

A pool admin deploying a curated pool (e.g., KYC-gated, institution-only) with `SwapAllowlistExtension` must add the router to the allowlist to support the standard periphery swap path. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the guard passes for **every** user who routes through it. The allowlist is completely neutralized. Unauthorized users trade on a pool that was supposed to exclude them, causing direct loss of curation value and potential regulatory/financial harm to the pool's LPs.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap entrypoint for EOAs. Any pool admin who wants end-users to be able to swap (rather than requiring direct pool calls) will add the router to the allowlist. This is the expected production configuration, making the bypass reachable in every realistic deployment of a swap-allowlisted pool.

### Recommendation

Gate on `recipient` (the actual economic beneficiary of the swap) instead of `sender` (the intermediary caller), mirroring how `DepositAllowlistExtension` gates on `owner`:

```solidity
// SwapAllowlistExtension.beforeSwap — fix
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, document explicitly that the allowlist gates the direct pool caller (router), not the end user, and require pool admins to enforce user-level restrictions off-chain or via a custom extension that decodes user identity from `extensionData`.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice; Bob is **not** allowlisted.
4. Bob calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
6. Extension receives `sender = router`; checks `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes successfully despite not being on the allowlist.

The invariant "only allowlisted addresses may swap on a curated pool" is broken by any user who routes through the supported periphery path.

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
