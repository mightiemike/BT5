### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Router Address Replaces Actual Swapper Identity - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router address**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any user can bypass the allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The router stores the original caller in transient storage for the payment callback but **never forwards it to the pool or the extension**. The extension has no way to distinguish Alice routing through the router from Bob routing through the router — both appear as `sender = router`.

This creates an inescapable dilemma for pool admins:

| Router allowlisted? | Legitimate user via router | Non-allowlisted attacker via router |
|---|---|---|
| No | Blocked (unusable router) | Blocked |
| Yes | Allowed | **Also allowed — bypass** |

Because the router is the primary user-facing swap interface, any pool admin who wants legitimate users to be able to use it must allowlist the router address, which simultaneously grants every non-allowlisted address the ability to bypass the gate.

### Impact Explanation

Pools configured with `SwapAllowlistExtension` are designed to restrict trading to specific parties (e.g., KYC-verified traders, institutional counterparties, or whitelisted market makers). Once the router is allowlisted — a necessary step for normal UX — any address can trade on the pool by routing through `MetricOmmSimpleRouter`. Unauthorized traders can execute swaps that extract value from LPs through adverse selection, front-running, or other strategies the allowlist was intended to prevent. The core access-control invariant of the pool is broken: LP principal is exposed to actors the pool admin explicitly excluded.

### Likelihood Explanation

The router is a public, permissionless contract. No special privileges, tokens, or setup are required beyond a normal swap call. The bypass is a single function call (`exactInputSingle` or `exactInput`) available to any EOA or contract. The only precondition is that the pool admin has allowlisted the router — which is the expected operational state for any pool that wants to support standard UX.

### Recommendation

The `sender` identity forwarded to extensions must reflect the **economically relevant actor**, not the intermediary. Two viable approaches:

1. **Router-level**: `MetricOmmSimpleRouter` should append the original `msg.sender` to `extensionData` in a standardized prefix slot, and `SwapAllowlistExtension.beforeSwap` should decode and verify it when `sender` is a known router.
2. **Extension-level**: Add a trusted-forwarder registry to `SwapAllowlistExtension` so that when `sender` is a registered router, the extension reads the actual user from a verified field in `extensionData` rather than accepting the router address as the gated identity.

Either approach must ensure the forwarded identity cannot be spoofed by an arbitrary caller supplying crafted `extensionData`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
//   pool has SwapAllowlistExtension as beforeSwap hook
//   allowedSwapper[pool][router] = true   (admin allowlists router for legitimate UX)
//   allowedSwapper[pool][attacker] = false (attacker is explicitly excluded)

// Attack — attacker calls the public router, not the pool directly:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:           address(pool),
        tokenIn:        token0,
        recipient:      attacker,
        deadline:       block.timestamp + 1,
        zeroForOne:     true,
        amountIn:       1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:  0,
        extensionData:  ""
    })
);
// pool.swap() is called with msg.sender = router
// extension.beforeSwap receives sender = router
// allowedSwapper[pool][router] == true  →  check passes
// attacker's swap executes on the allowlisted pool despite being excluded
``` [5](#0-4) [6](#0-5)

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
