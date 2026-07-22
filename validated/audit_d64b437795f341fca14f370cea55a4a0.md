### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every unprivileged user can bypass the per-user allowlist by routing through the router.

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool's `msg.sender` is the router contract address. The actual user (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the pool or to any extension. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, and to the recursive inner swaps in `_exactOutputIterateCallback`: [5](#0-4) [6](#0-5) 

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties). For any router-mediated swap to succeed, the admin must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, every user — including those explicitly excluded from the per-user allowlist — can call `exactInputSingle` or `exactInput` through the router and trade against the pool without restriction. The allowlist is completely defeated. Users who should be blocked can drain LP assets at oracle-fair prices, and the pool admin has no on-chain mechanism to enforce the intended curation policy while still supporting the standard periphery entry point.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented swap entry point for the protocol. Any pool that (a) configures `SwapAllowlistExtension` and (b) needs to support router-based swaps — the normal production configuration — is fully exposed. No special preconditions, flash loans, or multi-block timing are required. Any unprivileged user can trigger the bypass in a single transaction.

### Recommendation

The `sender` forwarded to extensions should represent the economic actor, not the immediate `msg.sender` of `pool.swap`. Two complementary fixes:

1. **Router-side**: Have the router pass the original `msg.sender` (the end user) as the `recipient`-equivalent "originator" through `extensionData`, and document that allowlist extensions must read it from there. This is an off-chain convention and fragile.

2. **Extension-side (preferred)**: Change `SwapAllowlistExtension.beforeSwap` to check `recipient` (the second argument) instead of `sender` when `sender` is a known router, or require the pool to carry an originator field. A cleaner solution is to have the router encode the real user address in `extensionData` and have the extension decode it, with a fallback to `sender` for direct calls.

3. **Core-level (most robust)**: Add an `originator` field to the pool's `swap` signature that the router populates with `msg.sender` before calling the pool, and have `ExtensionCalling` forward it to all hooks. This makes the real user identity available to every extension without relying on `extensionData` conventions.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Admin must also allowlist the router for router swaps to work.
swapAllowlist.setAllowedToSwap(pool, allowedUser, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true); // required for router path

// Attack: bannedUser bypasses the allowlist via the router.
vm.prank(bannedUser);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: bannedUser,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Succeeds: pool.swap sees msg.sender == router (allowlisted).
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
// bannedUser receives token1 output despite being excluded from the allowlist.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
