Audit Report

## Title
`SwapAllowlistExtension#beforeSwap` checks router address as `sender` instead of originating user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to restrict which addresses may swap in a pool. However, `MetricOmmPool.swap` passes `msg.sender` (the direct caller) as `sender` to the extension. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the router address, not the originating user. If the pool admin allowlists the router to enable approved users to swap through it, the allowlist gate is opened to every caller of the public, permissionless router.

## Finding Description
**Root cause 1 — `MetricOmmPool.swap`** passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

**Root cause 2 — `ExtensionCalling._beforeSwap`** forwards that `sender` (the direct pool caller) unchanged to every configured extension: [2](#0-1) 

**Root cause 3 — `SwapAllowlistExtension.beforeSwap`** evaluates `allowedSwapper[msg.sender][sender]`, which resolves to `allowedSwapper[pool][router]` for every router-mediated swap, regardless of who called the router: [3](#0-2) 

**Router call site — `exactInputSingle`**: the router calls `pool.swap(...)` directly, so `msg.sender` seen by the pool is the router: [4](#0-3) 

**Router call site — `exactInput` multi-hop**: intermediate hops use `address(this)` (the router) as payer, so `sender` is still the router for every hop: [5](#0-4) 

There is no mechanism in the router to forward the originating user's identity to the pool or extension. The `extensionData` field is passed through unchanged from the caller, but the extension does not decode it for identity — it reads only the `sender` parameter. No existing guard in the extension, pool, or router compensates for this mismatch.

## Impact Explanation
A pool admin deploying a pool with `SwapAllowlistExtension` (e.g., for KYC/regulatory compliance or curated LP access) must call `setAllowedToSwap(pool, router, true)` to allow approved users to use the public router. Once the router is allowlisted, `allowedSwapper[pool][router] == true` causes the extension to pass for every swap routed through it, regardless of who called the router. Any unprivileged address (Bob, not KYC'd) can call `router.exactInputSingle(...)` and execute a swap in the restricted pool. The allowlist provides zero protection once the router is allowlisted. This is a direct bypass of an admin-configured access control gate, exposing LP assets to unrestricted trading and defeating the purpose of the allowlist. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality" allowed impact criteria.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is a public, permissionless contract — any address can call it.
- The admin *must* allowlist the router to give approved users router access; there is no other mechanism.
- No special setup, flash loan, or privileged role is required for the attacker — only a token approval to the router.
- The bypass is reachable on every `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` call through the router.
- If the admin does not allowlist the router, approved users cannot use the router at all, making the periphery unusable for allowlisted pools (alternate broken invariant).

## Recommendation
The extension must gate the original user, not the intermediary. Two viable approaches:

1. **Router-forwarded identity in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it, trusting only a known router address as the forwarder. This requires the extension to maintain a trusted-router registry.

2. **Separate `originator` field in the pool interface**: Add an `originator` parameter to `pool.swap()` that the router sets to `msg.sender`. The extension checks `originator` instead of `sender`. This is a breaking interface change but is the cleanest fix.

Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router and should require direct pool interaction.

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. allowedSwapper[pool][alice]  = true   (approved user)
// 3. allowedSwapper[pool][router] = true   (so alice can use the router)
// 4. allowedSwapper[pool][bob]    = false  (bob is NOT approved)

function testBypassSwapAllowlistViaRouter() public {
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), alice, true);
    // bob is NOT allowlisted

    // Bob calls the router — extension sees sender=router, which IS allowlisted
    vm.startPrank(bob);
    token0.approve(address(router), type(uint256).max);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            recipient: bob,
            zeroForOne: true,
            amountIn: 1000e18,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    vm.stopPrank();

    // Bob's swap succeeded despite not being allowlisted
    assertGt(amountOut, 0, "Bob bypassed the swap allowlist via router");
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
