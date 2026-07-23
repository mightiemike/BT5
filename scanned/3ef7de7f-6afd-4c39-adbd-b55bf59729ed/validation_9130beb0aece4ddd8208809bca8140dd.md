### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the pool admin allowlists the router address (a natural action to permit "standard" router usage), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← always the immediate caller of swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool):

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router is the entity that calls `pool.swap`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

So `sender` seen by the extension is always the router address, never the end user. The pool admin has two choices:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert, even for allowlisted users |
| Allowlist the router | Every user — including those not individually allowlisted — can swap via the router |

There is no configuration that achieves the intended goal: allowing specific users to swap through the router while blocking others.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner passed explicitly by the pool) rather than `sender`. The swap extension has no equivalent "who is the economic actor" argument — it only has `sender`, which collapses to the router.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool and allowlists the `MetricOmmSimpleRouter` (a reasonable action to permit standard periphery usage) inadvertently opens the pool to all users. Any unprivileged address can execute swaps by routing through the router, draining LP value at oracle-quoted prices and bypassing the access control the pool was designed to enforce. This is a broken core pool functionality / admin-boundary break: the allowlist guard is misapplied and cannot gate individual users on the router path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing swap entry point. A pool admin who wants to allow "normal" router usage while restricting direct pool access will naturally allowlist the router. The admin has no on-chain signal that doing so opens the pool to all users. The bypass requires no special privileges — any address can call the router.

---

### Recommendation

The `beforeSwap` hook should receive the original end-user identity, not the immediate caller. Two options:

1. **Pass the payer/originator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is opt-in.

2. **Check `recipient` instead of `sender`** (if the pool's design intent is to gate who receives output): The `recipient` is set by the end user and is not collapsed to the router address.

3. **Mirror `DepositAllowlistExtension`**: Add an explicit "swapper" identity field to the swap hook interface (analogous to `owner` in `beforeAddLiquidity`) so the pool can pass the economically relevant actor separately from the immediate caller.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
3. Admin calls setAllowedToSwap(pool, router, true)      // router allowlisted for "standard" usage
4. charlie (not allowlisted) calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: charlie,
       ...
     })
5. Router calls pool.swap(charlie, ...) with msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
8. Swap executes — charlie bypasses the per-user allowlist
```

The extension checks `allowedSwapper[pool][router]` at step 7 instead of `allowedSwapper[pool][charlie]`, so the per-user gate is never evaluated. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
