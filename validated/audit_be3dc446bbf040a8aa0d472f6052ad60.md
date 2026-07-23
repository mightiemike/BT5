Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the end-user, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so `sender` is the router address, not the end user. Any pool admin who allowlists the router to enable router-mediated swaps inadvertently grants swap access to every user who calls the router, completely defeating the per-user allowlist.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user:** [3](#0-2) 

**The router never forwards the original user's address into the pool call; it calls `pool.swap()` directly with itself as `msg.sender`:** [4](#0-3) 

The router stores the original `msg.sender` only in transient storage as the `payer` for the callback, but this address is never passed into `pool.swap()` and is therefore invisible to extensions.

**Two broken scenarios result:**

1. **Allowlist bypass (primary impact):** Pool admin allowlists the router so router-mediated swaps work. Because the extension checks `allowedSwapper[pool][router]`, every user who calls the router passes the check regardless of individual allowlist status. The per-user gate is completely bypassed.

2. **Allowlisted users locked out of the router:** If the pool admin does NOT allowlist the router, individually allowlisted users cannot swap through the router at all — they must call `pool.swap()` directly, making the router unusable for curated pools.

**`DepositAllowlistExtension` does not share this flaw** because it gates by `owner` (the position owner argument passed explicitly), not by `sender` (the caller of `pool.addLiquidity`): [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker requires no special privilege — only a call to the public router. Unauthorized users can execute swaps against LP-owned token reserves at oracle-derived prices, causing direct loss of LP principal. This is a broken core allowlist invariant resulting in direct loss of user funds, qualifying as High severity under Sherlock thresholds.

## Likelihood Explanation

The router is the primary user-facing entrypoint for swaps. Any pool that enables router-mediated swaps must allowlist the router, which immediately opens the bypass to all users. The trigger requires no privileged access, no special token behavior, and no off-chain coordination — a single public `exactInputSingle` call suffices. The condition is not edge-case; it is the normal operating mode for any allowlisted pool that intends to support the router.

## Recommendation

Pass the original end-user address through the swap path so the extension can gate on it. Two approaches:

1. **Pool-side (preferred):** Add a `swapOnBehalf(address onBehalfOf, ...)` entry point to the pool, or have the pool accept an explicit `swapper` argument distinct from `msg.sender`, and pass that to extensions. The router would supply `msg.sender` as `swapper`.

2. **Router-side:** Have the router encode the original `msg.sender` into `extensionData` and have the extension decode it. This requires a convention between router and extension and is less robust.

3. **Extension-side workaround:** Document that `SwapAllowlistExtension` gates the direct caller of `pool.swap()`, and require pools using it to never allowlist the router — forcing allowlisted users to call the pool directly. This is a severe UX restriction.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured on beforeSwap.
// 2. Pool admin allowlists the router: swapExtension.setAllowedToSwap(pool, address(router), true)
// 3. Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] == false
// 4. Attacker calls router.exactInputSingle with the restricted pool.
//
// Result: extension checks allowedSwapper[pool][router] == true → swap succeeds.
// Attacker swaps on a pool they were never authorized to access.

function testBypassSwapAllowlistViaRouter() public {
    // admin allowlists only the router (to enable router-mediated swaps)
    vm.prank(admin);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // attacker is NOT individually allowlisted
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // attacker routes through the router — extension sees sender=router, passes
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
