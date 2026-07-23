Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool populates with its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router's address, not the originating EOA. Any pool admin who allowlists the router (required for allowlisted users to swap through it) simultaneously opens the gate to every user on the network, rendering the allowlist entirely ineffective for router-mediated swaps.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`MetricOmmPool.swap` populates `sender` with its own `msg.sender` — the direct caller of `pool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The same pattern applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). [4](#0-3) 

The extension therefore sees `sender = router`, not the originating EOA. The pool admin faces an impossible choice: not allowlisting the router blocks all allowlisted users from using it; allowlisting the router opens the gate to every user.

**Contrast with `DepositAllowlistExtension`**: the deposit extension correctly checks `owner`, which is an explicit argument that `MetricOmmPool.addLiquidity` accepts directly from the caller and preserves as the position owner. The swap path has no equivalent identity-preserving mechanism. [5](#0-4) 

## Impact Explanation
A curated pool using `SwapAllowlistExtension` (e.g., KYC-gated, institutional-only, or whitelist-restricted) can be fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. The attacker receives pool output tokens and the pool receives input tokens — a complete swap execution that the allowlist was supposed to prevent. This constitutes broken core pool functionality for allowlisted pools, directly violating the curation guarantee the extension is designed to enforce.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool operator who deploys `SwapAllowlistExtension` and also wants their allowlisted users to use the router (the normal operational assumption) will be vulnerable. No special attacker capability is required beyond calling a public router function with valid swap parameters.

## Recommendation
The `beforeSwap` hook must receive and check the originating user identity, not the intermediary. Two approaches:

1. **Pass the real initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires a coordinated convention between router and extension.
2. **Mirror the deposit pattern**: Add an explicit `initiator` field to the swap path (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before calling the pool, and the pool forwards to the extension as a dedicated argument.

The `DepositAllowlistExtension` already demonstrates the correct pattern — checking `owner` rather than `sender` — and the swap allowlist should adopt the same approach.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls swapExtension.setAllowedToSwap(pool, alice, true)
  - Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  1. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...) — router is msg.sender to pool
  3. Pool calls extension.beforeSwap(router, bob, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes; bob receives output tokens

Result:
  bob successfully swaps on an allowlisted pool without being allowlisted.
  The allowlist is entirely ineffective for router-mediated swaps.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
