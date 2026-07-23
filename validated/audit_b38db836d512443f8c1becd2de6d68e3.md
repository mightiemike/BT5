Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` at the pool's `swap` call boundary. When `MetricOmmSimpleRouter` mediates a swap, `sender` is the router's address, not the end-user's. A pool admin who allowlists the router (required for any approved user to trade through it) simultaneously opens the pool to every address on the network, completely defeating the allowlist.

## Finding Description

**Confirmed call chain:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly at line 72–80, making `msg.sender` at the pool equal to the router address. [1](#0-0) 

The pool's `_beforeSwap` in `ExtensionCalling` passes this `sender` (the router) verbatim into the extension hook: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — never the end-user: [3](#0-2) 

**The inescapable dilemma:**

| Router allowlisted? | Approved users can use router? | Non-approved users blocked? |
|---|---|---|
| No | ❌ | ✅ |
| Yes | ✅ | ❌ (any user passes) |

There is no configuration that simultaneously permits allowlisted users to trade through the router and blocks non-allowlisted users. The router's address is the only identity the extension ever sees.

## Impact Explanation

A pool admin deploying a restricted pool (KYC-only, institutional-only, whitelist-gated) who allowlists the canonical router so approved users can trade inadvertently opens the pool to every address on the network. Any non-allowlisted user can call `exactInputSingle`/`exactInput`/`exactOutputSingle`/`exactOutput` and the `beforeSwap` hook passes because it sees the allowlisted router address. This constitutes an admin-boundary break: the pool admin's access-control invariant is violated, LP funds in restricted pools are exposed to unrestricted toxic flow, and the allowlist mechanism is rendered entirely non-functional when the router is involved.

## Likelihood Explanation

The scenario is highly realistic and requires no special privilege. Pool admins deploying allowlisted pools will naturally allowlist the canonical router so their approved users can trade normally. The router is a public, permissionless contract — any user can call it. No flash loan, unusual token behavior, or special setup is required; a standard `exactInputSingle` call suffices. The bypass is silent — no revert, no distinguishing event.

## Recommendation

The extension must receive and check the end-user's address, not the immediate pool caller. The preferred fix is to have the router write the real `msg.sender` into a trusted transient-storage slot before calling `pool.swap`, and have the pool populate an `originSender` field in the hook arguments from that slot. The extension then checks `allowedSwapper[pool][originSender]`. Alternatively, the router can encode `msg.sender` into `extensionData` and the extension decodes and verifies it — though this requires careful trust assumptions. Until fixed, pool admins must be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

## Proof of Concept

```solidity
function test_swapAllowlist_routerBypass() public {
    // Pool admin allowlists the router so trustedUser can trade through it
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
    swapAllowlist.setAllowedToSwap(address(pool), trustedUser, true);
    // attacker is NOT allowlisted
    assertFalse(swapAllowlist.isAllowedToSwap(address(pool), attacker));

    vm.startPrank(attacker);
    token0.approve(address(router), type(uint256).max);
    // beforeSwap receives sender=router (allowlisted), passes — attacker swaps successfully
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            recipient: attacker,
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            tokenIn: address(token0),
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    vm.stopPrank();
    // Attacker successfully swapped in a pool they are not allowlisted for
}
```

### Citations

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
