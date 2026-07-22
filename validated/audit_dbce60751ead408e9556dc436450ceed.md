### Title
SwapAllowlistExtension gates the router address instead of the actual end-user, allowing any unprivileged caller to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router address (the only way to let allowlisted users use the router), every unprivileged user can bypass the per-user swap allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly. The pool therefore sees `msg.sender = router`, so `sender = router` is what the extension checks: [4](#0-3) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops through `_exactOutputIterateCallback`): [5](#0-4) [6](#0-5) 

The pool admin faces an inescapable dilemma:

- **Router not allowlisted**: allowlisted users cannot use the router at all (broken UX, broken swap flow).
- **Router allowlisted**: `allowedSwapper[pool][router] = true`, so the extension passes for every caller regardless of their individual allowlist status — the guard is fully bypassed.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

### Impact Explanation

Any unprivileged user can swap on a pool that is supposed to be restricted to a specific set of addresses (e.g., KYC-verified users, whitelisted institutions, or designated market makers). The attacker receives real output tokens from the pool at oracle-derived prices. If the pool is designed as a private venue with controlled counterparties, unauthorized swaps can drain liquidity, front-run allowlisted participants, or violate compliance requirements. The swap allowlist guard — the sole access-control mechanism on the swap path — is rendered inoperative for all router-mediated swaps.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap interface. Any pool admin who deploys a `SwapAllowlistExtension`-protected pool and wants allowlisted users to be able to use the router must allowlist the router address. Once that is done, the bypass is trivially reachable by any user with no special privileges, no flash loan, and no price manipulation — a single `exactInputSingle` call suffices.

### Recommendation

The extension must identify the true economic actor, not the immediate `msg.sender` of `pool.swap`. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated change to the router and extension.
2. **Check `recipient` instead of `sender`**: Gate on the address that receives the output tokens. This is only correct when the pool admin intends to restrict who may *receive* output, not who initiates the call.
3. **Dedicated router allowlist entry**: Document that the router must never be allowlisted and that allowlisted users must call `pool.swap` directly. This is a documentation/design fix but leaves the router unusable for restricted pools.

The cleanest fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData` for each hop, and `SwapAllowlistExtension` decodes the real caller from `extensionData` when `sender` is a known router.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; alice is allowlisted; router is allowlisted
// so that alice can use the router.

swapExtension.setAllowedToSwap(pool, alice, true);
swapExtension.setAllowedToSwap(pool, address(router), true); // required for alice to use router

// Attack: charlie (not allowlisted) routes through the router
vm.startPrank(charlie);
token0.approve(address(router), type(uint256).max);

// The extension sees sender = address(router), which IS allowlisted → passes
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: charlie,
        deadline: block.timestamp,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// charlie successfully swaps despite not being in the allowlist
vm.stopPrank();
```

`SwapAllowlistExtension.beforeSwap` receives `sender = address(router)`, finds it in `allowedSwapper[pool]`, and returns the success selector. Charlie's swap executes and he receives output tokens from the restricted pool.

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
