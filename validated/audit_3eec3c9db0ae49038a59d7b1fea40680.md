Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the end-user, allowing allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user, defeating the per-user allowlist entirely.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user:** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original user's address into the call arguments or `extensionData`:** [4](#0-3) 

The exploit path is: attacker calls `router.exactInputSingle()` → router calls `pool.swap()` with `msg.sender = router` → pool passes `router` as `sender` to `_beforeSwap` → extension checks `allowedSwapper[pool][router]` → if the router is allowlisted (required for any router-mediated swap to work), the check passes for every user regardless of individual allowlist status.

`DepositAllowlistExtension` does not share this flaw because it gates by `owner` (the explicit position owner argument passed by the caller), not by `sender` (the caller of the pool action): [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against LP-owned token reserves at oracle-derived prices, causing direct loss of LP principal. This is a broken core allowlist invariant resulting in direct loss of user/LP funds — matching the "allowlist bypass" and "broken core pool functionality causing loss of funds" impact categories at High severity.

## Likelihood Explanation

The router is the primary user-facing entrypoint for swaps. Any pool that enables router-mediated swaps must allowlist the router address, which immediately and unconditionally opens the bypass to all users. The trigger requires no privileged access, no special token behavior, and no off-chain coordination — a single public `exactInputSingle` call suffices. The condition (router allowlisted) is a necessary operational requirement for the router to function with allowlisted pools, making this effectively always-triggered in any realistic deployment.

## Recommendation

Pass the original end-user address through the swap path so the extension can gate on it. Two approaches:

1. **Pool-side (preferred):** Add a `swapOnBehalf(address onBehalfOf, ...)` entry point to the pool, or have the pool accept an explicit `swapper` argument distinct from `msg.sender`, and pass that to extensions. The router would supply `msg.sender` as `swapper`.

2. **Router-side:** Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires a trusted convention between router and extension, and the extension must verify the caller is a trusted router before accepting the decoded address.

3. **Extension-side workaround:** Document that `SwapAllowlistExtension` gates the direct caller of `pool.swap()` and prohibit allowlisting the router — forcing allowlisted users to call the pool directly. This is a severe UX restriction.

## Proof of Concept

```solidity
// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured on beforeSwap.
// 2. Pool admin allowlists the router: swapExtension.setAllowedToSwap(pool, address(router), true)
// 3. Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] == false
// 4. Attacker calls router.exactInputSingle with the restricted pool.
//
// Result: extension checks allowedSwapper[pool][router] == true → swap succeeds.
// Attacker swaps on a pool they were never authorized to access.

function testBypassSwapAllowlistViaRouter() public {
    vm.prank(admin);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    vm.prank(attacker);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            recipient: attacker,
            deadline: block.timestamp + 1,
            amountIn: 1000,
            amountOutMinimum: 0,
            zeroForOne: true,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // swap succeeds — allowlist bypassed
}
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
