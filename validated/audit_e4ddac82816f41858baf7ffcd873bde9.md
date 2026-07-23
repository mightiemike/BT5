### Title
`SwapAllowlistExtension` Gates Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which equals `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end-user. A pool admin who allowlists the router (a natural operational step to enable router-mediated swaps) inadvertently opens the pool to every user, bypassing the per-user restriction the allowlist was designed to enforce.

---

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding its own `msg.sender` as the `sender` argument to the extension. [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value and dispatches it to the configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` receives `sender` and checks it against the per-pool allowlist:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` with `msg.sender = router`: [4](#0-3) 

So the extension receives `sender = address(router)`, not the actual end-user. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router address (to permit router-mediated swaps), **every user** passes the check regardless of whether they are individually allowlisted.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the position owner), which is the economically relevant actor for deposits: [5](#0-4) 

The asymmetry confirms the swap-side check is the defective one.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, whitelisted market makers, or specific protocol contracts) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. LP funds are exposed to actors the pool admin explicitly intended to exclude. This is a direct, fund-impacting bypass of a core access-control invariant.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a natural and expected operational step: without it, no user can swap through the official router on an allowlisted pool. The admin faces a forced choice — either allowlist the router (opening the pool to all users) or leave it un-allowlisted (breaking router compatibility for all users). Either outcome is harmful. The bypass path is reachable by any unprivileged user once the router is allowlisted.

---

### Recommendation

The extension must check the **original end-user**, not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router**: `MetricOmmSimpleRouter` should encode `msg.sender` (the actual user) into `extensionData` or a dedicated field, and the extension should decode and check that value.

2. **Alternatively, check `sender` only when it is not a known router**: The extension can maintain a registry of trusted routers and, when `sender` is a router, extract the real initiator from `extensionData`.

The simpler and more robust fix is to have the router forward the original caller's address in `extensionData` and have `SwapAllowlistExtension` decode and gate on that value when present.

---

### Proof of Concept

**Setup:**
- Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
- Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
- Pool admin calls `setAllowedToSwap(pool, address(router), true)` — router is allowlisted to enable router-mediated swaps.

**Attack:**
- Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
- Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
- Pool calls `extension.beforeSwap(router, ...)`.
- Extension checks `allowedSwapper[pool][router]` → `true` → passes.
- Bob's swap executes successfully despite not being individually allowlisted.

**Expected:** Bob's swap reverts with `NotAllowedToSwap`.
**Actual:** Bob's swap succeeds, bypassing the per-user allowlist. [3](#0-2) [6](#0-5)

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
