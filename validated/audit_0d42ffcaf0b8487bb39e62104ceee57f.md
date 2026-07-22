### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap` call. When users route through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. Any pool admin who allowlists the router to enable router-mediated swaps for their curated users simultaneously opens the gate to every user on the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`MetricOmmPool.swap` sets `sender` to `msg.sender` of the pool call:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `exactInputSingle` on `MetricOmmSimpleRouter`, the router calls `pool.swap(params.recipient, ...)`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

`msg.sender` of that `pool.swap` call is the **router**, so `sender` delivered to the extension is the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The same mismatch applies to `exactInput` and `exactOutput` multi-hop paths. [4](#0-3) 

This is structurally opposite to `DepositAllowlistExtension`, which correctly ignores `sender` (the caller of `addLiquidity`) and checks `owner` — the economically relevant actor:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [5](#0-4) 

The `ExtensionCalling._beforeSwap` dispatcher faithfully forwards `sender` as the first positional argument, so the mismatch is not introduced there — it originates in the extension's choice of which argument to gate on. [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses must allowlist the router if they want those users to access the pool via the standard periphery. The moment the router is allowlisted, **every user** can bypass the per-user gate by routing through `MetricOmmSimpleRouter`. Unauthorized users can trade in pools intended for specific participants, draining liquidity at oracle-anchored prices, front-running allowlisted LPs, or violating compliance requirements of the curated pool. This is a direct loss-of-policy-control impact on LP principal.

---

### Likelihood Explanation

Medium-High. The bypass requires no special privileges. Any user who calls `exactInputSingle`, `exactInput`, or `exactOutput` on the public router triggers it. The precondition — the router being allowlisted — is the natural and expected configuration for any pool admin who wants their allowlisted users to use the standard periphery. The admin has no way to simultaneously allow router-mediated swaps for specific users and block others, because the extension cannot distinguish between them once the router is allowlisted.

---

### Recommendation

Gate on the actual initiating user rather than the immediate pool caller. Two concrete options:

1. **Check `recipient` instead of `sender`** — for single-hop swaps the recipient is often the user, though this breaks for multi-hop where intermediate recipients are the router itself.
2. **Decode the real initiator from `extensionData`** — the router already forwards caller-supplied `extensionData` unchanged to the pool; the extension can require the caller to ABI-encode their address in `extensionData` and verify it against `msg.sender` of the router call (passed through the extension payload). This is the most robust approach and mirrors how Uniswap v4 hooks handle the originator problem.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only user A is intended to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so user A can use the router.
4. User B (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(params.recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, recipient, ...)`.
7. `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]` → `true`.
8. User B's swap executes successfully in the curated pool, bypassing the per-user allowlist entirely.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
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
