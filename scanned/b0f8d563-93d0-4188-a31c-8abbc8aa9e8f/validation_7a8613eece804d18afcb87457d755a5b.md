### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` is the caller, the pool sets `sender = msg.sender = router`, so the extension checks the router's address against the allowlist instead of the actual end-user's address. Any user who routes through the public router bypasses the per-user allowlist entirely.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly with no forwarding of the original caller: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router contract address**, so the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The actual end-user's address is never seen by the guard.

This creates an irreconcilable contradiction for any pool admin who wants to use the allowlist with the router:

- If the router is **not** allowlisted: allowlisted users cannot swap through the router at all.
- If the router **is** allowlisted: every user on the network can bypass the allowlist by routing through the router.

The same structural problem applies to multi-hop `exactInput` (where intermediate hops use `address(this)` as payer) and `exactOutput`. [5](#0-4) 

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for restricting swap access to approved counterparties (e.g., KYC'd users, whitelisted market makers, or private pools). When the router is allowlisted — the only configuration that lets approved users trade through the standard periphery — the guard is fully neutralised: any address can call `exactInputSingle` and execute a swap on the restricted pool. This is a broken core pool functionality / admin-boundary break with direct fund-impact consequences: liquidity providers who deposited into a private pool expecting restricted counterparties are exposed to unrestricted public trading.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public entry point for swaps. Any pool that enables the `SwapAllowlistExtension` and also allowlists the router (the only way to let approved users use the standard UX) is immediately exploitable by any address. No special privileges, flash loans, or unusual token behaviour are required — a single `exactInputSingle` call suffices.

### Recommendation

The extension must gate on the **original end-user**, not on the immediate pool caller. Two complementary fixes:

1. **Pass the original caller through the router.** Add an optional `originSender` field to the extension data or a dedicated router-forwarded argument so the extension can verify the true initiator. The router would encode `msg.sender` into `extensionData` and the extension would decode and check it.

2. **Check `recipient` as a proxy.** For single-hop swaps the recipient is often the user; however this is not reliable for multi-hop or third-party recipient patterns.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when the immediate `sender` is a known router, similar to how Uniswap v4 uses `msgSender` in hook contexts.

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists the router so that approved users can trade.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// blockedUser is NOT individually allowlisted.
// blockedUser calls the router directly — the extension sees sender=router, which IS allowed.
vm.startPrank(blockedUser);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    tokenOut:        address(token1),
    zeroForOne:      true,
    amountIn:        1_000,
    amountOutMinimum: 0,
    recipient:       blockedUser,
    deadline:        block.timestamp + 1,
    priceLimitX64:   0,
    extensionData:   ""
}));
// Swap succeeds — blockedUser bypassed the allowlist.
assertGt(token1.balanceOf(blockedUser), 0);
vm.stopPrank();
```

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
