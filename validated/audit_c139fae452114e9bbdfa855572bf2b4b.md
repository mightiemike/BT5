Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently grants every caller of the router unrestricted access to the restricted pool, completely defeating the allowlist.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The only way for a pool admin to let allowlisted users trade through the router is to call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check passes for every caller of the router regardless of whether that caller is on the allowlist, because the extension never inspects the originating user address.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin explicitly controls who may trade. The allowlist is the sole on-chain enforcement mechanism for that curation. When the router is allowlisted (the only supported path for permitted users to trade through the standard periphery), the guard degrades to a no-op: any EOA or contract can call `exactInputSingle` on the router and execute a swap against the restricted pool. This is a complete bypass of the swap allowlist, allowing unpermissioned users to trade on pools designed to be restricted. The consequence is direct loss of the curation guarantee and potential fund-impacting trades (e.g., arbitrage or front-running by actors the pool admin explicitly excluded).

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap path. Any pool admin who deploys `SwapAllowlistExtension` and also wants their allowlisted users to use the standard router must allowlist the router — there is no other supported mechanism. This makes the vulnerable configuration the expected production configuration, not an edge case. The bypass requires no special privileges: any EOA or contract can call the router.

## Recommendation

The extension must check the original user, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. Requires coordinated changes to both the router and the extension.

2. **Check `recipient` instead of `sender`**: The pool already passes `recipient` as the second argument to `beforeSwap`. Checking `allowedSwapper[pool][recipient]` is not spoofable by the router and correctly identifies the beneficiary of the trade. This is the simpler fix and requires no router changes:

```solidity
function beforeSwap(
    address,          // sender (router) — ignored
    address recipient,
    bool, int128, uint128, uint256, uint128, uint128,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

## Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension as EXTENSION_1,
   BEFORE_SWAP_ORDER = 1 (extension 1 runs before every swap).
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — the only way to let allowlisted users trade through the router.
3. Pool admin does NOT call setAllowedToSwap(pool, alice, true)
   — alice is explicitly excluded.

Attack
──────
4. alice calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: alice, ...}).
5. Router calls pool.swap(recipient=alice, ...) — msg.sender to pool = router.
6. Pool calls _beforeSwap(sender=router, recipient=alice, ...).
7. Extension evaluates allowedSwapper[pool][router] == true → passes.
8. Swap executes; alice receives output tokens.

Expected: revert NotAllowedToSwap (alice is not on the allowlist).
Actual:   swap succeeds because the router is allowlisted.
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
