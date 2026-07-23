### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the end user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps on curated pools to specific, allowlisted addresses. However, the identity it checks is the pool's immediate `msg.sender` — which is the `MetricOmmSimpleRouter` contract when users route through it — rather than the actual end user. If the pool admin allowlists the router to support router-mediated swaps (a natural operational step), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← pool's immediate caller
    recipient,
    zeroForOne,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = pool's msg.sender
    )
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

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

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool's `msg.sender` is now the **router address**, not the end user. The allowlist check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

A pool admin who wants to support router-based swaps must allowlist the router. Once the router is allowlisted, the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted. Any non-allowlisted user can bypass the restriction by routing through `MetricOmmSimpleRouter`.

This is structurally different from `DepositAllowlistExtension`, which correctly gates the economically relevant actor — the position `owner` — rather than the immediate caller:

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The deposit guard checks `owner` (passed explicitly and immutably by the pool), so routing through `MetricOmmPoolLiquidityAdder` does not change the gated identity. The swap guard has no equivalent "end user" parameter — it only receives the pool's `msg.sender` — making it structurally unable to distinguish end users when they share a common router intermediary.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router to support standard periphery usage inadvertently opens the pool to all users. Any non-allowlisted address can execute swaps on the restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). This defeats the curation policy entirely, allows unauthorized parties to drain LP-owned token reserves at oracle-derived prices, and constitutes a direct loss of LP principal on pools that were designed to be restricted.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly documented swap entry point. Pool admins who configure a swap allowlist and also want to support normal user flows will naturally allowlist the router. The bypass requires no special knowledge, no privileged access, and no unusual token behavior — any EOA can call the router. The trigger is a routine operational decision by a semi-trusted pool admin who does not realize that allowlisting the router collapses the per-user gate.

---

### Recommendation

Pass the end user's identity through the swap path so the extension can gate on it. Two concrete options:

1. **Add a `swapper` parameter to `swap`** (analogous to `owner` in `addLiquidity`) that the pool passes to `_beforeSwap` alongside `msg.sender`. The router would forward `msg.sender` as `swapper`. Extensions gate on `swapper` rather than `sender`.

2. **Encode the end user in `extensionData` with a signature** and verify it inside `beforeSwap`. The router would inject `msg.sender` into the extension payload before forwarding to the pool.

Either approach aligns the swap allowlist with the deposit allowlist's correct pattern of gating the economically relevant actor rather than the immediate contract caller.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  pool admin calls: swapExt.setAllowedToSwap(pool, alice, true)
  pool admin calls: swapExt.setAllowedToSwap(pool, router, true)   ← enables router-based swaps

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
      → pool.msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓
          → swap proceeds
  bob receives output tokens from the restricted pool
  alice's allowlist entry is irrelevant; the router entry is the only check that matters
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
