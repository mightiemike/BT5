Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. Because `MetricOmmPool.swap` passes its own `msg.sender` as `sender`, and `MetricOmmSimpleRouter` is the direct caller of `pool.swap`, the extension checks the router's address rather than the end user's address. Any pool admin who allowlists the router to support legitimate router-based swaps inadvertently grants every user — including non-allowlisted ones — the ability to bypass the curated pool's access control.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender`:** [1](#0-0) 

`msg.sender` here is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter`, that is the router contract address.

**`SwapAllowlistExtension.beforeSwap` checks `sender` (the router) against the allowlist:** [2](#0-1) 

`msg.sender` inside `beforeSwap` is the pool (the `onlyPool` caller), and `sender` is the argument forwarded from `pool.swap` — i.e., the router address, not the end user.

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making itself `msg.sender`:** [3](#0-2) 

The actual end user (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the payment callback; it is never forwarded to the pool as `sender`.

**Exploit path:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only specific addresses.
2. To support router-based swaps for those addresses, the admin calls `setAllowedToSwap(pool, routerAddress, true)`.
3. An attacker (not on the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool.
4. The router calls `pool.swap(...)` — `msg.sender` of `pool.swap` is the router.
5. `beforeSwap` receives `sender = routerAddress`, checks `allowedSwapper[pool][routerAddress]` → `true` → swap proceeds.
6. The attacker successfully swaps on a pool they were never meant to access.

**Existing guards are insufficient:** The `onlyPool` modifier on `beforeSwap` only ensures the pool is the caller; it does not validate that `sender` represents the true end user. There is no mechanism in the pool or router to pass the original `msg.sender` through to the extension.

## Impact Explanation
The swap allowlist is the sole access-control mechanism for curated pools. Its bypass means any unprivileged user can trade on a pool restricted to a specific set of addresses. This breaks a core pool functionality (access-gated swaps) and constitutes an admin-boundary break: the pool admin's intent to restrict swap access is fully defeated by routing through a public contract. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, whitelist-only liquidity), this can result in unauthorized fund flows and loss of LP principal through unwanted trades.

## Likelihood Explanation
The condition requires the pool admin to have allowlisted the router address — a natural and expected action for any pool that wants to support router-based swaps for its allowlisted users. Once the router is allowlisted (which is the normal operational state), the bypass is trivially reachable by any user with no special privileges, no capital requirements beyond the swap amount, and is fully repeatable.

## Recommendation
The extension should check the true end user, not the intermediary caller. Two complementary approaches:

1. **Pass the original caller through `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when `sender` is a known router. This requires trust in the router encoding.

2. **Check `recipient` instead of (or in addition to) `sender`:** For router flows the recipient is often the end user, though this is not always the case.

3. **Preferred — add an `originator` field to the swap interface:** Propagate the true end user from the router to the pool and then to extensions, similar to how Uniswap v4 uses `hookData` or a dedicated `msgSender` field.

The cleanest fix is to have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension` decode and verify it, with the router being a trusted forwarder registered in the factory.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. Allowlist only `legitimateUser` and `router` (to support router swaps for legitimate users)
// 3. Attempt swap as `attacker` through the router

function test_allowlistBypassViaRouter() public {
    // Admin allowlists the router so legitimate users can use it
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT allowlisted
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // Attacker routes through the router — pool sees router as sender, bypass succeeds
    vm.prank(attacker);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Swap succeeds — attacker bypassed the allowlist
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
