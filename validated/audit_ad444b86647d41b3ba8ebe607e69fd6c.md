### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the allowlist checks the router's address rather than the actual end user's address. If the router is allowlisted (a natural admin action to enable router-based swaps), every user on the network can bypass the curated pool's swap restriction.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the router contract, not the end user. The allowlist therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

A pool admin who wants to allow router-based swaps will add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[msg.sender][sender]` passes for every caller because `sender` is always the router regardless of who initiated the transaction. The per-user curation is completely defeated.

The same structural mismatch exists for `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap()` from the router contract. [5](#0-4) 

Note: `DepositAllowlistExtension` does **not** share this flaw because it gates on the `owner` parameter (the explicit position owner passed through `addLiquidity`), which `MetricOmmPoolLiquidityAdder` correctly sets to the user-supplied `owner` address rather than to `msg.sender`. [6](#0-5) 

### Impact Explanation

Any user can trade on a curated pool that is supposed to restrict swaps to a specific set of addresses. The attacker receives pool output tokens at oracle-anchored prices, draining the pool's inventory in directions the pool admin intended to block. This is a direct loss of LP principal and a complete failure of the allowlist invariant.

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router — a natural and expected configuration step for any pool that wants to support the official periphery. No privileged attacker capability is needed beyond calling the public router. The router is a deployed, permissionless contract.

### Recommendation

The `beforeSwap` hook must identify the actual end user, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Decode from `extensionData`**: Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires the router and extension to agree on an encoding convention.
2. **Gate on `recipient` with a convention**: Require that on allowlisted pools the `recipient` must equal the allowlisted user and that the router enforces `recipient == msg.sender`. This is fragile and not recommended.

The cleanest fix is approach 1: the router encodes `abi.encode(msg.sender)` into `extensionData` for allowlisted pools, and the extension decodes and checks that address instead of the raw `sender` parameter.

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)      // enable router-based swaps for alice
4. Bob (not allowlisted) calls router.exactInputSingle({pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender = router
6. beforeSwap receives sender = router; allowedSwapper[pool][router] == true → passes
7. Bob's swap executes at oracle price, receiving pool tokens he was not authorized to receive.
```

Step 3 is the realistic admin action that opens the bypass. Without it, allowlisted users cannot use the router at all, which is a broken-functionality impact in the other direction.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
