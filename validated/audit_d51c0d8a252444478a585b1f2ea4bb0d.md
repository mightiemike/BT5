### Title
SwapAllowlistExtension gates the router address instead of the real user, allowing any caller to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end-user. A pool admin who allowlists the router to support router-mediated swaps inadvertently grants every user on-chain access to the restricted pool, completely defeating the allowlist guard.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry-point) calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

Because the router is a shared, permissionless contract, a pool admin who adds the router to the allowlist (the only way to let any user reach the pool through the standard periphery) simultaneously grants every user on-chain the ability to pass the guard. The allowlist entry `allowedSwapper[pool][router] = true` is equivalent to `allowAllSwappers[pool] = true` for any caller who knows the router address.

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for restricting pool access to a curated set of swappers (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market-makers). When the router is allowlisted, the guard is silently nullified: any unprivileged address can execute swaps against the restricted pool by routing through `MetricOmmSimpleRouter`. This is a direct admin-boundary break — an unprivileged path bypasses a configured access-control gate — and allows unauthorized parties to interact with pool liquidity that was intended to be restricted.

### Likelihood Explanation

The trigger requires no special privilege. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is in the allowlist (both facts are readable on-chain from `allowedSwapper` and pool immutables) can immediately route through the router. The pool admin's only alternative — not allowlisting the router — breaks the standard periphery UX for all legitimate users, making the allowlist-plus-router configuration the natural operational choice and therefore the common vulnerable state.

### Recommendation

The extension must identify the real end-user, not the intermediary. Two viable approaches:

1. **Require the actual user in `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData`; the extension decodes and checks that address. This requires a coordinated change to the router and the extension.

2. **Check `sender` only for direct (non-router) calls and reject router calls entirely**: The extension reverts if `sender` is a known router address, forcing allowlisted users to call the pool directly.

The simplest safe fix is approach 1: the extension reads the real user from `extensionData` and verifies `allowedSwapper[pool][realUser]`, while the router always encodes `msg.sender` into the `extensionData` slot consumed by this extension.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only intended swapper
  allowedSwapper[pool][router] = true         // admin adds router to support periphery UX

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., extensionData: ""})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient, zeroForOne, amount, limit, "", "")
          msg.sender = router
        → _beforeSwap(sender=router, ...)
          → extension.beforeSwap(sender=router, ...)
              allowedSwapper[pool][router] == true  ✓  (check passes)
        → swap executes, bob receives output tokens

Result: bob swaps successfully despite never being allowlisted.
``` [3](#0-2) [1](#0-0) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
