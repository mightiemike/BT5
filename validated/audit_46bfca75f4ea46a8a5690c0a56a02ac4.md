### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. If the pool admin allowlists the router address (the natural step to enable router-mediated swaps for their allowlisted users), every unprivileged user can bypass the allowlist by routing through the router.

### Finding Description

**Call chain:**

```
User (non-allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, ...)
          // msg.sender inside pool = router address
          → _beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  // checks allowedSwapper[pool][router] → TRUE → passes
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called the pool — the router, not the end user: [3](#0-2) 

The router calls `pool.swap` directly with no forwarding of the original `msg.sender`: [4](#0-3) 

This creates an irresolvable dilemma for the pool admin:

| Admin configuration | Effect |
|---|---|
| Allowlist only individual user addresses | Allowlisted users **cannot** use the router (router not in list → reverts) |
| Allowlist the router address | **Every** user can bypass the allowlist via the router |

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users.

### Impact Explanation

Any non-allowlisted user can trade on a curated pool by routing through `MetricOmmSimpleRouter`. The pool admin's intended access control (e.g., KYC gate, institutional-only pool, partner-only liquidity) is silently voided. The non-allowlisted user receives the full swap output at oracle prices, draining pool liquidity that was reserved for authorized participants. This is a direct loss of LP assets and a broken core pool invariant (the allowlist guard fails open on the supported public periphery path).

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap interface for end users. Any pool admin who wants their allowlisted users to be able to use the router (the normal UX path) must allowlist the router address. Once that is done, the bypass is trivially reachable by any address with no special privileges. The trigger is a single public call to `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`.

### Recommendation

The `sender` argument passed to extensions must reflect the **economic initiator** of the swap, not the immediate caller of `pool.swap`. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should pass the original `msg.sender` (the end user) as `callbackData` or a dedicated field so the pool can forward it to extensions.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should check the `recipient` or a user-supplied identity field rather than the raw `sender` when the `sender` is a known router/aggregator, **or** the pool should expose a dedicated "originator" slot that periphery contracts fill before calling `swap`.

The simplest immediate fix is to have the router store the original caller in transient storage (analogous to how it already stores the payer in `_setNextCallbackContext`) and have the pool read and forward it as a separate `originator` argument to extensions.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin also allowlists the router so allowedUser can use the UI.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);

// Attack: non-allowlisted attacker bypasses the gate via the router.
vm.startPrank(attacker); // attacker NOT in allowlist
token0.approve(address(router), type(uint256).max);

// Direct call → correctly blocked
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, true, 1000, type(uint128).max, "", "");

// Router call → allowlist bypassed (router is allowlisted, sender = router)
uint256 out = router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: address(token0),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// out > 0 — attacker successfully swapped on a curated pool
vm.stopPrank();
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
