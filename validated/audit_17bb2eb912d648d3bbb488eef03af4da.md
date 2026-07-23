The code confirms all three key claims. Let me verify the complete call chain:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` — confirmed at lines 230-231.
2. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — confirmed at line 37.
3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no encoding of the original `msg.sender` into `extensionData` — confirmed at lines 72-80.

The bypass is real and the precondition (router must be allowlisted for router-mediated swaps to work) is a necessary operational configuration, not an edge case.

---

Audit Report

## Title
`SwapAllowlistExtension::beforeSwap` checks the router address instead of the originating user, allowing any user to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the original user. A pool admin who allowlists the router (the only way to enable router-mediated swaps for any user) inadvertently grants every caller of the router the ability to bypass the allowlist, silently voiding the pool's curation policy.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, passing the direct caller as `sender`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level, with no encoding of the original user into `extensionData`: [3](#0-2) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) [5](#0-4) 

The extension never sees the originating user — it sees the router. A pool admin who wants to allow router-mediated swaps for allowlisted users must add the router to `allowedSwapper`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router, regardless of whether that caller is on the allowlist. The `allowedSwapper` mapping and `allowAllSwappers` flag are the only guards: [6](#0-5) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd market makers, whitelisted protocols, or internal addresses) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker pays no special cost and needs no privileged access. The pool's curation policy is silently voided, allowing unauthorized users to trade against pool liquidity, extract favorable oracle-driven pricing, or interact with pools whose terms were never intended for them. This constitutes broken core pool functionality and a direct access control bypass.

## Likelihood Explanation
The likelihood is high. `MetricOmmSimpleRouter` is the standard periphery entry point. Pool admins who want their allowlisted users to use the router must allowlist the router address — this is the expected operational configuration, not an edge case. Once that configuration is in place, the bypass is trivially reachable by any user with no preconditions.

## Recommendation
The extension must gate the originating user, not the direct pool caller. Two viable approaches:

1. **Pass the originating user through `extensionData`:** The router encodes `msg.sender` (the original user) into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address.

2. **Update `SwapAllowlistExtension.beforeSwap`** to decode the originating user from `extensionData` when present, and update `MetricOmmSimpleRouter` to always encode `msg.sender` into the extension payload:

```solidity
// In SwapAllowlistExtension.beforeSwap:
address effectiveSender = extensionData.length >= 20
    ? abi.decode(extensionData, (address))
    : sender;
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][effectiveSender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted, alice NOT allowlisted

// 1. Pool admin allowlists the router (required for router-mediated swaps)
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 2. alice is NOT allowlisted
assertFalse(swapExtension.isAllowedToSwap(address(pool), alice));

// 3. alice calls pool.swap() directly → reverts (correct behavior)
vm.prank(alice);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(alice, true, 1000, type(uint128).max, "", "");

// 4. alice routes through MetricOmmSimpleRouter → succeeds (bypass!)
// The extension sees sender = address(router), which IS allowlisted
vm.prank(alice);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: alice,
        tokenIn: address(token1),
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// alice's swap succeeds despite not being on the allowlist
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
