### Title
SwapAllowlistExtension gates on `sender` (the immediate pool caller) rather than the actual user, allowing any user to bypass per-user swap restrictions via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the immediate `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that value is the router's address, not the actual user's address. A pool admin who allowlists the router to enable router-based swaps inadvertently allows every user to bypass the per-user swap allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller), so the check resolves to `allowedSwapper[pool][sender]`. The value of `sender` is whatever `msg.sender` was when `pool.swap()` was called. [1](#0-0) 

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [2](#0-1) [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is called, the router is the entity that calls `pool.swap()`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The actual user (`msg.sender` to the router) is stored only in transient storage for the payment callback — it is **never passed** to `pool.swap()` and therefore never reaches the extension. The extension sees `sender` = router address.

This creates an irreconcilable conflict for any pool admin who wants to:
1. Allow specific users to swap through the router.
2. Block non-allowlisted users from swapping.

To enable router-based swaps for any user, the admin must allowlist the router address. But allowlisting the router address passes the check for **every** user who routes through it, because the extension cannot distinguish between individual users at that point.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position owner), which is correctly propagated through `MetricOmmPoolLiquidityAdder` as the actual beneficiary regardless of who the immediate caller is. [5](#0-4) 

---

### Impact Explanation

Any user can bypass the swap allowlist of a curated pool by routing through `MetricOmmSimpleRouter`, provided the router is allowlisted. This violates the access control invariant of the pool. In a pool designed for specific institutional counterparties, unauthorized users gaining swap access exposes LPs to adverse selection at oracle prices and breaks the curation guarantee the pool admin configured. This matches the "allowlist bypass through router" impact class explicitly listed in the audit scope.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router address. A pool admin who wants their allowlisted users to be able to use the router (a natural and expected use case) would allowlist the router, inadvertently opening the pool to all users. The mistake is easy to make because the admin has no other mechanism to selectively enable router-based swaps for specific users.

---

### Recommendation

The extension must be able to identify the actual user, not the immediate pool caller. Concrete options:

1. **Trusted router pattern**: Have the router encode the actual user in `extensionData` and have the extension decode and verify it (requires the router to be trusted by the extension).
2. **Recipient

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
