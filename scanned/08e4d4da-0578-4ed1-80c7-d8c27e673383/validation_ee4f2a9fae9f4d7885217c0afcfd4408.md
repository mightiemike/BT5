### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct caller of `MetricOmmPool.swap`. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the actual end user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps), every user who calls through the router bypasses the per-user allowlist gate entirely.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool), and `sender` is whatever `msg.sender` was when `MetricOmmPool.swap` was entered. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

So `msg.sender` inside `pool.swap` is the **router**, not the end user. The extension therefore evaluates:

```
allowedSwapper[pool][router]
```

not `allowedSwapper[pool][endUser]`. The actual end user identity is never checked.

The same path exists for `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

---

### Impact Explanation

If the pool admin allowlists the router address — a natural action when the pool is intended to be accessible through the standard periphery — the check `allowedSwapper[pool][router] == true` passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted. The per-user access control boundary is completely bypassed.

LPs in a restricted pool are then exposed to trades from any counterparty, defeating the purpose of the allowlist (e.g., KYC gating, institutional-only pools, adverse-selection mitigation). Because swaps consume LP liquidity at oracle prices, unauthorized high-frequency or informed traders can systematically extract value from LPs who believed they were protected.

Conversely, if the pool admin does **not** allowlist the router, individually allowlisted users cannot swap through the router at all — the allowlist is broken in the opposite direction, making the pool unusable through the standard periphery.

---

### Likelihood Explanation

The bypass is reachable by any unprivileged user with no special setup beyond calling the public router. The only precondition is that the pool admin allowlists the router address, which is the expected operational step for any pool that intends to be accessible through the standard `MetricOmmSimpleRouter`. The pool admin has no on-chain signal that allowlisting the router grants access to all router users rather than to the router as a trusted intermediary.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **actual end user**, not the direct caller of the pool. Two options:

1. **Pass the original initiator through the router.** The router already stores the original `msg.sender` in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`). Expose it as a field in `extensionData` or a dedicated transient slot so the extension can read the true initiator.

2. **Check `tx.origin` as a fallback.** Less clean but immediately effective for EOA-only allowlists; does not work for smart-contract swappers.

The `DepositAllowlistExtension` correctly gates on `owner` (the position owner), which is the economically relevant identity for deposits. The swap allowlist needs the equivalent fix: gate on the economically relevant swapper identity, not the intermediary. [5](#0-4) 

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin allowlists the router: `swapExtension.setAllowedToSwap(pool, address(router), true)`.
3. A non-allowlisted EOA `attacker` calls `router.exactInputSingle({pool: pool, ...})`.
4. Inside `pool.swap`, `msg.sender == router`; the extension evaluates `allowedSwapper[pool][router] == true` → passes.
5. The attacker's swap executes in a pool that should have blocked them.

Conversely, if step 2 instead allowlists `attacker` directly (`setAllowedToSwap(pool, attacker, true)`), the attacker's router call still fails because `sender == router` is not allowlisted — demonstrating that the allowlist is non-functional for router-mediated swaps in either configuration. [6](#0-5) [2](#0-1) [7](#0-6)

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
