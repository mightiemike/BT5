Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating EOA, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the immediate caller of `pool.swap`. When `MetricOmmSimpleRouter` intermediates a swap, the pool's `msg.sender` is the router contract, not the originating EOA. Any pool admin who allowlists the router to permit router-mediated swaps for legitimate users simultaneously grants unrestricted swap access to every non-allowlisted EOA.

## Finding Description
The call chain is confirmed by the production code:

1. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

2. `ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — not the originating EOA: [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with no forwarding of the originating EOA into `extensionData` or any other field: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The structural trap is binary and unavoidable under the current design: if the router is not allowlisted, no allowlisted EOA can use the router; if the router is allowlisted, every non-allowlisted EOA can bypass the restriction. There is no configuration that achieves both goals simultaneously.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any EOA can execute swaps on the pool by routing through `MetricOmmSimpleRouter`, receiving oracle-priced output tokens. This is a direct policy bypass with fund-impacting consequences: LP assets are exposed to unrestricted trading by actors the pool admin explicitly intended to exclude. This meets the "admin-boundary break bypassed by an unprivileged path" and "broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool admin deploying a curated pool who wants to support standard front-ends or aggregators will allowlist the router. Once the router is allowlisted, the bypass is reachable by any EOA with a single `exactInputSingle` call — no special timing, flash loan, or multi-step setup is required.

## Recommendation
The extension must gate the economically relevant actor — the originating EOA — not the immediate pool caller. Two viable approaches:

1. **Pass the original caller through the router:** Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when `sender` is a known router address.
2. **Introduce an `originalSender` field in the hook signature** at the pool level, populated by the pool from a dedicated parameter or from `extensionData` set by the router.

Option 1 is implementable without core changes: the router appends the originating EOA to `extensionData`, and the extension reads it when `sender` matches a registered router address.

## Proof of Concept
```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so allowlisted users can use it
extension.setAllowedToSwap(pool, address(router), true);
// Alice (allowlisted EOA) is also set:
extension.setAllowedToSwap(pool, alice, true);
// Bob (non-allowlisted EOA) is NOT set.

// Attack: Bob routes through the router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             pool,
        recipient:        bob,
        tokenIn:          token0,
        zeroForOne:       true,
        amountIn:         1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:    0,
        deadline:         block.timestamp,
        extensionData:    ""
    })
);
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
// Bob receives oracle-priced token1 output despite not being on the allowlist.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
