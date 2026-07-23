### Title
SwapAllowlistExtension Gates the Router's Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the user. If the pool admin allowlists the router (a necessary step for legitimate router-mediated swaps to work), every user on the network can bypass the individual allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded from `MetricOmmPool.swap`, which is `msg.sender` of the pool call. When a user calls the pool directly, `sender = user`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)`, so `sender = address(router)`. [2](#0-1) 

The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [3](#0-2) [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an inescapable dilemma:

| Router allowlisted? | Allowlisted user (direct) | Non-allowlisted user (direct) | Non-allowlisted user (via router) |
|---|---|---|---|
| No | ✓ passes | ✗ blocked | ✗ blocked — but allowlisted users also cannot use the router |
| Yes | ✓ passes | ✗ blocked | **✓ passes — bypass** |

The `DepositAllowlistExtension` does not have this flaw: it checks `owner` (the position owner, the economically relevant actor), not `sender` (the caller of the pool): [5](#0-4) 

The swap extension checks the wrong identity.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., only known market makers, to prevent adverse selection or MEV). To allow those counterparties to use the official periphery router, the admin calls `setAllowedToSwap(pool, router, true)`. At that point, **any address** can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool, bypassing the individual allowlist entirely. LP funds are exposed to the full universe of traders the allowlist was designed to exclude, leading to direct loss of LP principal through adverse selection.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public periphery entry point. Pool admins who want allowlisted users to be able to use the router must allowlist it. The bypass is then reachable by any unprivileged address with no special setup. The only precondition is that the pool admin has taken the natural operational step of allowlisting the router.

---

### Recommendation

Gate the **actual user** rather than the pool's immediate caller. The extension should require the caller to attest the real user identity, or the pool should forward the original `tx.origin`-equivalent through a trusted periphery path. The simplest fix mirrors the deposit extension: define a canonical "real swapper" field (analogous to `owner` for deposits) and check that field. Alternatively, the extension can require that `sender` never be a known router address and that routers forward the originating user in `extensionData`, which the extension then decodes and checks.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is meant to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router] == true`, and passes.
7. Bob's swap executes against the pool despite never being individually allowlisted. [1](#0-0) [6](#0-5)

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
