### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. The extension therefore checks the router's address against the allowlist, not the real trader's address. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged address can bypass the allowlist by routing through the same router.

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool.swap()
                   → _beforeSwap(msg.sender=router, recipient, ...)
                        → ExtensionCalling._beforeSwap(sender=router, ...)
                             → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                                  → allowedSwapper[pool][router]  ← wrong actor checked
```

`MetricOmmPool.swap` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender` to the pool: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

### Impact Explanation

Two failure modes exist, both fund-impacting:

**Mode A – Router allowlisted (allowlist bypass):** The pool admin allowlists the router so that their curated users can trade through the standard periphery. Because the extension only sees the router address, every address in the world can now call `exactInputSingle` through the router and pass the allowlist check. The curated pool's access control is completely nullified; any trader can execute swaps and drain LP value at oracle-derived prices.

**Mode B – Router not allowlisted (router unusable):** If the admin does not allowlist the router, every router-mediated swap reverts with `NotAllowedToSwap` even for addresses that are individually allowlisted. The pool's primary periphery entry point is broken for all users, constituting broken core swap functionality.

Mode A is the direct-loss path: an unprivileged user bypasses a live access-control guard and executes swaps the pool designer explicitly intended to block.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented swap entry point for end users. Any pool that deploys `SwapAllowlistExtension` and also wants to support router-mediated swaps must allowlist the router, which immediately opens Mode A. The trigger requires no special privilege — any EOA can call `exactInputSingle`. The pool admin's own configuration step (allowlisting the router) is what activates the bypass.

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the **economically responsible actor**, not the immediate caller of `pool.swap`. Two complementary fixes:

1. **Pass the original initiator through the extension payload.** The router should encode `msg.sender` (the real user) into `extensionData` and the extension should decode and check that address instead of (or in addition to) the `sender` argument.

2. **Alternatively, check both `sender` and the decoded initiator.** If `sender` is a known router, require that the router-supplied initiator is allowlisted; if `sender` is an EOA, check `sender` directly.

The `DepositAllowlistExtension` does not share this bug because it gates on `owner` (the position owner explicitly supplied by the caller), which is the correct economic actor for deposits. [6](#0-5) 

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended gated user
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  pool.swap(msg.sender=router, ...)
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap(sender=router)
    → allowedSwapper[pool][router] == true  ✓  (passes!)

  bob's swap executes at oracle price — allowlist completely bypassed.
```

The fix is to encode the real initiator (`msg.sender` of the router call) into `extensionData` and have the extension verify that address, not the router's address.

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
