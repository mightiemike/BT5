Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, but `sender` is `msg.sender` of the pool's `swap()` call — which is the router contract when users route through `MetricOmmSimpleRouter`, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, the allowlist is completely bypassed for every user on-chain, as any caller of the router will pass the check.

## Finding Description

**Root cause — allowlist checks router identity, not end-user:**

`SwapAllowlistExtension.beforeSwap` at line 37 checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. [1](#0-0) 

**What the pool passes as `sender`:**

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [2](#0-1) 

**What the router passes to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool: [3](#0-2) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [4](#0-3) [5](#0-4) [6](#0-5) 

**The dilemma for the pool admin:**

1. **Do not allowlist the router** → legitimate allowlisted users cannot swap through the router (extension sees the router address and reverts).
2. **Allowlist the router** → the allowlist is completely bypassed; any user can call `exactInputSingle` on the router and the extension passes because `allowedSwapper[pool][router] == true`.

There is no mechanism in the current design to thread the original end-user address through the router to the extension. The `sender` field is structurally bound to the direct caller of `pool.swap()`.

**Existing tests do not cover this path:**

The unit tests for `SwapAllowlistExtension` only test direct pool calls (not router-mediated calls), so the bypass is not caught by the existing test suite: [7](#0-6) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. Once the pool admin allowlists the router (the natural action to support router-mediated swaps), the allowlist provides zero protection. Unauthorized users can execute swaps against the pool's LP positions at oracle-anchored prices, causing direct loss of LP principal. This is a broken core pool functionality causing loss of funds — the allowlist guard, a primary access-control mechanism, is rendered completely ineffective.

## Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys a restricted pool and then tries to make it usable via the router will naturally add the router to the allowlist. The misconfiguration is not obvious from the extension's interface or documentation, and the unit tests for `SwapAllowlistExtension` only test direct pool calls, so the bypass is not caught by the existing test suite. The attack requires no special privileges — any unprivileged on-chain address can call `MetricOmmSimpleRouter.exactInputSingle`.

## Recommendation

The `beforeSwap` hook should receive and check the **original end-user address**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original payer through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before calling the pool. `SwapAllowlistExtension` decodes and checks this address. This requires a convention between the router and the extension.

2. **Add an `originator` field to the swap interface**: Extend `IMetricOmmPoolActions.swap()` with an explicit `originator` parameter that the router sets to `msg.sender` (the end user). The pool passes this to `_beforeSwap` alongside `sender`. The extension checks `originator` instead of `sender`.

Option 2 is cleaner and avoids relying on `extensionData` conventions. Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router and should document that router-mediated swaps are incompatible with the allowlist guard.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow legitimate users to swap via the router).
  - Pool admin does NOT add attacker's address to the allowlist.

Attack:
  1. Attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
       pool=restrictedPool, tokenIn=..., amountIn=..., ...
     ).
  2. Router calls restrictedPool.swap(recipient, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives output tokens.

Result:
  Attacker bypasses the allowlist and swaps on a restricted pool,
  extracting LP value at oracle-anchored prices without authorization.

Foundry test outline:
  - Deploy SwapAllowlistExtension, pool, and MetricOmmSimpleRouter.
  - Admin calls extension.setAllowedToSwap(pool, router, true).
  - Prank as attacker (not in allowlist).
  - Call router.exactInputSingle(...) targeting the restricted pool.
  - Assert swap succeeds (no NotAllowedToSwap revert).
  - Assert attacker received output tokens.
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
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
