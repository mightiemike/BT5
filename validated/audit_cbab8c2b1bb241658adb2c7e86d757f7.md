### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. A pool admin who allowlists the router address to enable router-based swaps inadvertently grants every user on-chain the ability to bypass the per-user restriction entirely.

### Finding Description

`SwapAllowlistExtension.beforeSwap()` performs its access check as:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the value the pool passes from its own `msg.sender` — i.e., whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

the `msg.sender` seen by the pool is the **router address**, so `sender` forwarded to the extension is the router, not the end user. The allowlist therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates two mutually exclusive failure modes:

**Mode A – Bypass**: The pool admin allowlists the router address so that router-based swaps work. Every user on-chain can now call `router.exactInputSingle()` and pass the allowlist check, because the router is allowlisted. The per-user restriction is completely defeated.

**Mode B – Lockout**: The pool admin allowlists individual user addresses (not the router). Those users can call `pool.swap()` directly and pass, but any attempt to route through `MetricOmmSimpleRouter` reverts with `NotAllowedToSwap`, because the router is not on the list. Allowlisted users lose access to multi-hop, exact-output, and native-ETH swap paths.

The asymmetry with `DepositAllowlistExtension` makes the oversight clear. Deposits explicitly separate the operator (`sender`) from the beneficiary (`owner`) and check `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The deposit hook correctly checks the position owner regardless of who the operator/router is. The swap hook has no equivalent mechanism; it only has `sender` (the direct caller) and `recipient` (the output destination), neither of which reliably identifies the economic actor initiating the swap through a router.

### Impact Explanation

**Mode A (Bypass)**: Any unprivileged user can execute swaps in a pool that the admin intended to restrict to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or internal protocol contracts). Unauthorized swaps against an oracle-priced pool expose LP funds to adverse selection, front-running, or deliberate drain if the oracle price lags. This is a direct loss path for LP principal.

**Mode B (Lockout)**: Allowlisted users cannot access router-based swap paths (multi-hop, exact-output, native ETH). Core swap functionality is broken for the intended user set.

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` with the intent of restricting swaps to a named set of users is affected the moment a user routes through `MetricOmmSimpleRouter`. The router is the standard periphery entry point documented for end users. A pool admin who follows the natural configuration path — allowlist the router so users can swap — triggers Mode A immediately. No special privileges, no malicious setup, and no non-standard tokens are required. The trigger is a normal `exactInputSingle` call.

### Recommendation

Mirror the deposit pattern: introduce an explicit end-user identity that survives router indirection. Two options:

1. **Preferred**: Add a `swapper` parameter to the `beforeSwap` hook (analogous to `owner` in `beforeAddLiquidity`) that the pool populates from a caller-supplied argument rather than from `msg.sender`. The router would forward `msg.sender` as the swapper, and the allowlist would check that value.

2. **Minimal**: Document that `SwapAllowlistExtension` gates the direct caller of `pool.swap()`, not the end user, and that pool admins must allowlist each router/aggregator address rather than individual users — accepting that this grants all users of that router access.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured in `beforeSwap` order. Admin intends to restrict swaps to address `ALICE`.
2. Admin calls `swapExtension.setAllowedToSwap(pool, ALICE, true)`. `ALICE` is allowlisted; all others are not.
3. `BOB` (not allowlisted) calls `router.exactInputSingle({pool: pool, ..., recipient: BOB})`.
4. Router calls `pool.swap(BOB, ...)` — `msg.sender` seen by the pool is `address(router)`.
5. Pool calls `_beforeSwap(sender=address(router), ...)`.
6. `SwapAllowlistExtension.beforeSwap(sender=address(router), ...)` evaluates `allowedSwapper[pool][address(router)]`.
7. If the admin previously allowlisted the router (Mode A): check passes, `BOB`'s swap executes — allowlist bypassed.
8. If the admin did not allowlist the router (Mode B): `ALICE` also cannot swap through the router even though she is individually allowlisted — core swap path broken. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
