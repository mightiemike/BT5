### Title
SwapAllowlistExtension Gates the Router Address Instead of the Ultimate User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the originating EOA. To permit any router-mediated swap on an allowlisted pool the admin must add the router to the allowlist, but doing so silently grants every user on the network the ability to bypass the allowlist by routing through that same public contract.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router becomes the direct caller of `pool.swap()`: [4](#0-3) 

So the allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][EOA]`.

Contrast this with `DepositAllowlistExtension`, which correctly checks the `owner` parameter (the economically relevant actor) rather than the `sender` (the direct caller): [5](#0-4) 

### Impact Explanation

Two mutually exclusive failure modes arise for any pool that uses `SwapAllowlistExtension` and also expects users to route through `MetricOmmSimpleRouter`:

1. **Allowlist bypass (fund-impacting)**: To let allowlisted users swap via the router, the admin must add the router to `allowedSwapper`. Because the router is a public, permissionless contract, every address on the network can now call `exactInputSingle` / `exactInput` / `exactOutput*` and pass the allowlist check. The curated pool's swap restriction is completely nullified.

2. **Broken core functionality**: If the admin does not add the router, allowlisted EOAs cannot use the router at all, breaking the standard swap path for legitimate users.

Either outcome is fund-impacting: in case 1, unauthorized actors can drain LP value from a pool that was designed to trade only with trusted counterparties; in case 2, legitimate users are locked out of the supported periphery path.

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint in the periphery layer and is expected to be used for all production swaps.
- Any pool that deploys `SwapAllowlistExtension` to restrict access (e.g., a private institutional pool) will immediately encounter this issue the first time an allowlisted user attempts a router-mediated swap.
- No special privileges or unusual conditions are required; a standard `exactInputSingle` call is sufficient to trigger the bypass once the router is allowlisted.

### Recommendation

The swap allowlist must gate the **originating user**, not the intermediary. Two complementary fixes:

1. **In the router**: pass the originating `msg.sender` as an additional field in `extensionData` (or a dedicated parameter) so extensions can recover the true user identity.
2. **In `SwapAllowlistExtension.beforeSwap`**: decode the originating user from `extensionData` and check `allowedSwapper[pool][originatingUser]` instead of `allowedSwapper[pool][sender]`.

Alternatively, mirror the deposit extension's pattern: introduce a `swapRecipient` or `swapOriginator` concept that the pool tracks separately from the direct caller, and gate the allowlist on that value.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)   // only Alice may swap
  - Admin calls setAllowedToSwap(pool, router, true)  // needed so Alice can use the router

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
        pool=pool, tokenIn=token0, recipient=bob, ...
    )
  - pool.swap() is called with msg.sender = router
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully, bypassing the allowlist
```

The check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router]` — the router address — rather than `allowedSwapper[pool][bob]`. Because the router is allowlisted (required for Alice), Bob's swap passes unchallenged.

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
