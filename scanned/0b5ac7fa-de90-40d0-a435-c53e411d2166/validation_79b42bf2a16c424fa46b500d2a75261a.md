### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to work on that pool), every unpermissioned user can bypass the allowlist by calling the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows: [1](#0-0) 

The `sender` argument it receives is forwarded verbatim from `ExtensionCalling._beforeSwap`, which passes `msg.sender` of the pool's `swap` call: [2](#0-1) [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside `MetricOmmPool.swap` is the **router contract**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router_address]`.

For any allowed user to reach the pool through the router, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router_address]` passes for **every** caller of the router, regardless of whether that caller is on the intended allowlist. Any unpermissioned user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and the extension will approve the swap.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly supplied by the caller), not on `sender`: [5](#0-4) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that protection entirely for any user who routes through the public `MetricOmmSimpleRouter`. The bypassing user can execute swaps at oracle-anchored prices against LP capital that was deposited under the assumption that only approved counterparties could trade. This constitutes a direct policy bypass with fund-impacting consequences for LPs on curated pools.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery entry point for swaps. A pool admin who deploys a swap-allowlisted pool and wants to support router-mediated swaps for their approved users has no choice but to allowlist the router, which simultaneously opens the pool to all users. The scenario is reachable by any unprivileged user with no special setup beyond calling the public router.

### Recommendation

The extension must gate on the **originating user**, not the immediate pool caller. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Check `sender` against a router allowlist and then verify the real user from transient storage**: The extension, when `sender` is a known router, reads the payer stored in the router's transient context (e.g., via `IMetricOmmSimpleRouter`) to obtain the actual user and checks that address against `allowedSwapper`.

The simplest safe fix is to remove router support from allowlisted pools entirely and document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` unless the pool intends to allow all router users.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, ALICE, true)   // only ALICE is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required for router-mediated swaps

Attack:
  - BOB (not on allowlist) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) → msg.sender in pool = router
  - _beforeSwap passes sender = router to SwapAllowlistExtension
  - Extension checks allowedSwapper[pool][router] → true (router is allowlisted)
  - BOB's swap executes successfully, bypassing the allowlist
``` [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
