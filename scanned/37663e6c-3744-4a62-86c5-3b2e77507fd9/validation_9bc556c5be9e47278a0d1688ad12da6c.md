### Title
SwapAllowlistExtension gates on router address instead of end-user identity, enabling allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` to the pool — the direct caller of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the end user. If a pool admin allowlists the router to support router-mediated swaps for authorized users, every user who routes through the router bypasses the per-user restriction entirely.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension dispatcher.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the `sender` field in the ABI-encoded call to every configured extension: [2](#0-1) 

**Step 2 — SwapAllowlistExtension keys its check on that `sender`.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`msg.sender` here is the pool (correct); `sender` is whoever called `pool.swap()`.

**Step 3 — MetricOmmSimpleRouter is always the direct caller of `pool.swap()`.**

For `exactInputSingle` and `exactOutputSingle`, the router calls the pool directly: [4](#0-3) 

For multi-hop `exactInput`, every hop is called by the router: [5](#0-4) 

In all cases `msg.sender` seen by the pool — and therefore `sender` seen by the extension — is `address(MetricOmmSimpleRouter)`, not the end user.

**Step 4 — The resulting catch-22.**

| Pool admin action | Effect |
|---|---|
| Allowlist only individual users (e.g. `alice`) | Alice cannot swap through the router (router is not allowlisted); she must call the pool directly. |
| Allowlist the router to support router-based swaps | Every user — including unauthorized ones — can swap through the router, because the extension sees `sender = router` and the router is allowlisted. |

There is no configuration that allows specific users to swap through the router while blocking others. The allowlist collapses to an all-or-nothing gate on the router contract itself.

**Step 5 — Exact exploit path (bypass scenario).**

1. Pool admin deploys pool with `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — intending only alice to trade.
3. Admin calls `setAllowedToSwap(pool, router, true)` — intending to let alice use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Extension evaluates `allowedSwapper[pool][router]` →

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
