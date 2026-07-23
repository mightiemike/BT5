Audit Report

## Title
SwapAllowlistExtension gates the router contract address instead of the originating user, allowing full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, `sender` is the router contract address, not the originating user. A pool admin who allowlists the router to support router-mediated swaps for curated users inadvertently opens the pool to every user, because the extension cannot distinguish individual users behind the router.

## Finding Description

**Root cause — three confirmed code facts:**

**1.** `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender` to `_beforeSwap`: [1](#0-0) 

**2.** `ExtensionCalling._beforeSwap` forwards `sender` verbatim to the extension via `abi.encodeCall`: [2](#0-1) 

**3.** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]` — when called via the router: [3](#0-2) 

**4.** `MetricOmmSimpleRouter.exactInputSingle` stores the originating user's address in transient storage for the payment callback only, and calls `pool.swap()` directly — making `msg.sender` in the pool equal to the router address, not the user: [4](#0-3) 

The originating user's address (`msg.sender` of the router call) is stored via `_setNextCallbackContext` exclusively for the payment callback and is never visible to the extension. The `params.extensionData` passed to the pool is user-supplied and not authenticated, so it cannot be used as a trust anchor by the extension in its current form.

**Exploit flow:**

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    // natural action to support router-mediated swaps for allowlisted users

Attack:
  attacker (not in allowedSwapper[pool]) calls:
    MetricOmmSimpleRouter.exactInputSingle({ pool: pool, recipient: attacker, ... })

  Router calls: pool.swap(attacker, zeroForOne, amount, limit, "", extensionData)
    msg.sender = router

  Pool calls: _beforeSwap(sender=router, ...)

  SwapAllowlistExtension.beforeSwap:
    allowedSwapper[pool][router] == true  ← passes
    swap executes against LP funds

Result:
  Attacker swaps on a curated pool without being individually allowlisted.
```

**Why existing guards fail:** The only guard is `allowedSwapper[pool][sender]`. When `sender` is the router, the check collapses to a single bit for all users behind the router. There is no mechanism in the current extension or pool interface to recover the originating user's address from transient storage or `extensionData` in an authenticated way.

## Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-controlled addresses). Once the pool admin allowlists the router to support the standard periphery flow, any unprivileged user can execute swaps on the curated pool by routing through `MetricOmmSimpleRouter`. The allowlist protection is fully bypassed for all router-mediated swaps, allowing unauthorized users to trade against LP funds at oracle-derived prices. This constitutes broken core pool functionality with direct exposure of LP assets to unintended counterparties — a fund-impacting impact matching the allowed impact gate.

## Likelihood Explanation

Allowlisting the router is the natural and expected action for any pool admin who wants their curated pool to be usable through the standard periphery. `MetricOmmSimpleRouter` is the documented swap entry point. A pool admin who does not allowlist the router renders the router unusable for their allowlisted users, which is a non-obvious and undocumented restriction. The bypass is therefore reachable on any curated pool that supports router-mediated swaps, which is the common deployment pattern. No special privileges are required — any unprivileged address can call `exactInputSingle`.

## Recommendation

The extension must gate the originating user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it. This requires the router to be trusted to supply the correct value, which is acceptable since the router is a known periphery contract. The extension should also verify `msg.sender` (the pool) is a known pool to prevent spoofing.

2. **Trusted forwarder mapping**: Add a `trustedForwarder` mapping to the extension. When `sender` is a trusted forwarder (e.g., the router), decode the real user from `extensionData` and apply the allowlist check against that address. When `sender` is not a trusted forwarder, apply the check directly against `sender`.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, only alice is allowlisted
    swapExtension.setAllowedToSwap(pool, alice, true);
    // Admin allowlists router to support alice's router-mediated swaps
    swapExtension.setAllowedToSwap(pool, address(router), true);

    // Attack: attacker (not allowlisted) swaps via router
    vm.startPrank(attacker);
    token0.approve(address(router), type(uint256).max);
    // This should revert but does not — router address passes the allowlist check
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: attacker,
        tokenIn: address(token0),
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    vm.stopPrank();
    // Attacker received token1 from a pool they are not allowlisted on
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
