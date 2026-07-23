Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap()` passes `msg.sender` (the router contract address) as `sender` to the extension. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`, allowing any user to bypass the curated-pool allowlist by routing through the router.

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user swaps through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(...)` directly — making the router `msg.sender` to the pool: [4](#0-3) 

So `sender = router_address`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. This creates two symmetric failure modes:

1. **Allowlist bypass**: If the router is allowlisted (the expected production configuration), every user — including those explicitly blocked — can bypass the allowlist by routing through the router.
2. **Allowlisted users blocked**: If the router is not allowlisted, individually allowlisted users cannot trade through the router at all.

The `DepositAllowlistExtension` does not share this bug because it checks `owner` (the position owner explicitly passed by the caller), not `sender`: [5](#0-4) 

The existing tests in `SwapAllowlistSubExtension.t.sol` and `FullMetricExtension.t.sol` only test direct pool calls, never router-mediated swaps, so the bypass is not caught: [6](#0-5) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers) provides no real restriction. Any address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactOutputSingle` targeting the pool and the extension will check the router's allowlist status, not the caller's. This is a direct, unprivileged bypass of the pool's primary access-control mechanism, allowing unauthorized users to trade against LP funds in a pool designed to be curated. This constitutes broken core pool functionality and direct loss of access-control integrity for LP funds.

## Likelihood Explanation

The router is the canonical user-facing entrypoint for swaps. Any production deployment of a curated pool that also allows router access (the normal case) is immediately exploitable by any address. No special setup, flash loan, or privileged role is required — a single `exactInputSingle` call suffices. The exploit is repeatable and unprivileged.

## Recommendation

The extension must check the economically relevant actor. The simplest safe fix consistent with the existing design is to require that any allowlisted router passes the real user's address in `extensionData`, and the extension decodes and checks that address when `sender` is a known trusted router. Alternatively, the pool admin configures trusted routers and the extension decodes the real user from the payload when `sender` is a trusted router. A weaker alternative is checking `tx.origin`, but this is not recommended for general use.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the only allowed swapper)
  - allowedSwapper[pool][router] = true  (router must be allowlisted for normal use)

Attack (as bob, who is NOT allowlisted):
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient, ...) — msg.sender to pool = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob successfully traded on a pool he was not allowed to access

Result: allowlist is completely bypassed for any user who routes through the router.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
