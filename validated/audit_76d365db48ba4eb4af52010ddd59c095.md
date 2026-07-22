### Title
`SwapAllowlistExtension` checks router address instead of user address, allowing any user to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than whether the **user** is allowlisted. A pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the allowlist to every user who calls the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — i.e., whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is a forced choice for any pool admin who deploys `SwapAllowlistExtension`:

| Admin decision | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the internet can bypass the allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

### Impact Explanation

Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (KYC, institutional, whitelist-only) and also needs to support the public router loses its access-control guarantee. A non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the restricted pool. The router calls `pool.swap()`; the extension sees `sender = router_address`; if the router is allowlisted, the check passes and the swap executes. The non-allowlisted user receives pool output tokens and the pool receives input tokens under terms the admin intended to restrict. This is a direct policy bypass with fund-impacting consequences: the pool may be drained of arbitrage profit, subjected to front-running, or used by actors the admin explicitly excluded.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. The bypass requires only that the pool admin has allowlisted the router — a natural and expected configuration for any pool that wants to support router-mediated swaps for its allowlisted users. The attack requires no special privileges, no flash loans, and no complex setup: one `exactInputSingle` call suffices.

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economic actor** (the end user), not the intermediary contract. Two complementary fixes:

1. **In the router**: pass the original `msg.sender` (the user) as the `recipient`-equivalent "originator" through a dedicated field, or use `tx.origin` as a fallback (with its known limitations).

2. **Preferred — in the extension**: change `SwapAllowlistExtension.beforeSwap` to check the `recipient` argument (second parameter) when `sender` is a known router, or require pools to configure the extension with a list of trusted forwarders that must supply the real user identity via `extensionData`.

3. **Simplest safe fix**: document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the factory level by reverting if both are configured together, until a proper originator-forwarding mechanism is implemented.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (extension1) in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (bob is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=bob, ...)  →  msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true  ✓
  5. Swap executes; bob receives output tokens.

Result: bob, who is not on the allowlist, successfully swaps on a curated pool.
``` [6](#0-5) [7](#0-6) [1](#0-0)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
