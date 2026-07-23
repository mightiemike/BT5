Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender` to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. Any pool admin who allowlists the router so that their permitted users can access the standard periphery simultaneously grants every unpermitted user the ability to bypass the allowlist with a single router call.

## Finding Description

`MetricOmmPool.swap` captures `msg.sender` and passes it as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and forwards it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address the pool received as its `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool's `msg.sender` equal to the router contract: [4](#0-3) 

The actual user's address is stored only in transient callback context via `_setNextCallbackContext` for payment settlement and is never forwarded to the pool as `sender`: [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. This creates an inescapable dilemma: if the pool admin does **not** allowlist the router, allowlisted users cannot use the standard periphery; if the admin **does** allowlist the router, every non-allowlisted user bypasses the guard by routing through the same router. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [6](#0-5) 

## Impact Explanation
A pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers) is fully bypassed the moment the router is allowlisted. Any unprivileged user can execute swaps against the pool's liquidity, causing LP providers to trade with counterparties the pool was explicitly configured to exclude. This is a direct breach of the pool's curation invariant: unauthorized swap execution drains LP value through trades the pool was designed to reject. The impact is a broken core pool functionality causing loss of funds and an unusable allowlist control flow.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical swap interface for the protocol. Pool admins who deploy `SwapAllowlistExtension` will naturally allowlist the router so their permitted users can access the standard periphery. The bypass is then reachable by any public user with a single router call — no special privileges, no multi-step setup, no flash loan required. The precondition (router allowlisted) is the expected operational state for any pool using this extension with the standard router.

## Recommendation
The extension must check the identity of the economic actor, not the immediate caller. Two sound approaches:

1. **Router-forwarded identity**: Modify `MetricOmmSimpleRouter` to encode `msg.sender` into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`. This must be applied consistently across `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, and the recursive `_exactOutputIterateCallback` path.
2. **Pool-level originator field**: Add an explicit `originator` parameter to `pool.swap` that the router populates with `msg.sender`. The pool forwards it to extensions alongside `sender`. Extensions that need to gate the economic actor check `originator`.

## Proof of Concept
```
1. Deploy MetricOmmPool with SwapAllowlistExtension as extension1.
2. Pool admin calls setAllowedToSwap(pool, alice, true)      // allowlist Alice
3. Pool admin calls setAllowedToSwap(pool, router, true)     // allowlist router so Alice can use it
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, zeroForOne: true, amountIn: X, ...})
5. Router calls pool.swap(recipient, true, X, ...) — msg.sender of pool call = router
6. Pool calls _beforeSwap(sender=router, ...)
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
8. Bob's swap executes successfully despite not being allowlisted.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
