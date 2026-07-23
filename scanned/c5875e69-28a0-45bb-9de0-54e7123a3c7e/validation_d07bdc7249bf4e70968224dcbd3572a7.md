### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted (required for any router-mediated swap to work), every user — including those not on the allowlist — can bypass the guard by going through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `exactInputSingle` or `exactInput` on `MetricOmmSimpleRouter`, the router is the entity that calls `pool.swap()`: [4](#0-3) [5](#0-4) 

In every router path (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), `pool.swap` is called with `msg.sender = router`. The allowlist therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for pool admins:

- **If the router is allowlisted** (necessary for any router-mediated swap to work on the curated pool): every user, including those not on the allowlist, can bypass the guard by routing through the router.
- **If the router is not allowlisted**: allowlisted users cannot use the router at all, breaking the primary supported swap entry point.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers) can be fully bypassed by any user calling `MetricOmmSimpleRouter.exactInputSingle`. The attacker gains unauthorized access to swap on a pool that was explicitly designed to exclude them. This constitutes a direct policy bypass with fund-impacting consequences: the pool's LP positions are exposed to trades from actors the pool admin explicitly intended to exclude, which may violate regulatory requirements, risk parameters, or LP agreements.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary documented user-facing entry point for swaps. Any user who observes that a pool has a swap allowlist can trivially attempt the bypass by calling the router instead of the pool directly. No special privileges, flash loans, or multi-transaction setup are required. The bypass is reachable in a single transaction.

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economically responsible actor — the end user — not the intermediary contract. Two viable fixes:

1. **Preferred**: Add an `originator` field to the swap call path (analogous to how `exactInput` already tracks the original `msg.sender` as the payer in `_setNextCallbackContext`). Pass the original caller through to the extension hook so the allowlist can check the true initiator.

2. **Alternative**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the extension or factory level (e.g., revert if `msg.sender` is a known router). This is weaker because it requires maintaining a router registry.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists `alice` (`allowedSwapper[pool][alice] = true`) and the router (`allowedSwapper[pool][router] = true`) so that alice can use the router.
3. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true`.
6. The swap executes successfully for `bob`, who was never allowlisted.

Alternatively, even without step 2, if the pool admin only allowlists `alice` directly (not the router), `alice` herself cannot use the router because the check would see `sender=router` which is not allowlisted — demonstrating the broken invariant in both directions.

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
