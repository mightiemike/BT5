Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the original user, breaking the per-user swap allowlist for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, that value is the router address, not the original user. The allowlist lookup therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, making the per-user gate either permanently broken (allowlisted users cannot use the router) or trivially bypassable (if the router is allowlisted, all users pass).

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` at lines 230–231:

```solidity
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` (lines 149–177) ABI-encodes and forwards that `sender` unchanged to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` (line 37) then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool address (used as the pool key) and `sender` = the direct caller of `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle` (lines 72–80), the router calls `pool.swap()` directly, so `sender` = router address. The extension checks `allowedSwapper[pool][router]`, never consulting the original user's entry. There is no mechanism in the call chain to thread the original EOA address through to the extension.

## Impact Explanation
Two concrete, mutually exclusive failure modes arise for any pool deploying `SwapAllowlistExtension`:

| Scenario | Result |
|---|---|
| Admin allowlists `userA`; `userA` swaps via router | `allowedSwapper[pool][router]` = false → revert with `NotAllowedToSwap`; allowlisted user is permanently blocked from the primary public swap interface |
| Admin allowlists the router to unblock users | `allowedSwapper[pool][router]` = true → every user passes; per-user gate is fully bypassed |

This constitutes broken core pool functionality: the extension's stated purpose (gating `swap` by swapper address, per pool) is unachievable for any router-mediated swap, which is the standard public path.

## Likelihood Explanation
The router is the standard public swap path. Any pool that deploys `SwapAllowlistExtension` and expects users to swap via `MetricOmmSimpleRouter` hits this immediately on the first router-mediated swap attempt. No special attacker setup, privileged role, or non-standard token behavior is required. The broken behavior is triggered by normal usage.

## Recommendation
Thread the original user's identity explicitly through the call chain. One approach: add a `swapper` field to the `beforeSwap` hook signature that the pool populates from a separate source (e.g., `extensionData` passed by the router, or a dedicated transient storage slot written by the router before calling `pool.swap()`). The router already stores the original `msg.sender` in transient storage via `_setNextCallbackContext` (line 71 of `MetricOmmSimpleRouter.sol`); a similar mechanism could expose it to extensions. Alternatively, `SwapAllowlistExtension` could be redesigned to check `recipient` if the pool's design guarantees the recipient is the economic beneficiary, but the cleanest fix is to pass the original user address explicitly.

## Proof of Concept
```solidity
// Pool has SwapAllowlistExtension; only userA is allowlisted.
swapExtension.setAllowedToSwap(address(pool), userA, true);

// userA swaps directly → passes (sender = userA ✓)
vm.prank(userA);
pool.swap(userA, false, 1000, type(uint128).max, "", "");

// userA swaps via router → REVERTS (sender = router, not allowlisted)
vm.prank(userA);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: userA,
    ...
}));
// ↑ reverts with NotAllowedToSwap even though userA is allowlisted

// Admin allowlists the router to "fix" it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// userB (NOT allowlisted) bypasses the gate via router:
vm.prank(userB);
router.exactInputSingle(...); // succeeds — allowlist fully bypassed
```

**Supporting code references:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` at the pool boundary: [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` (= router) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to every extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the router, not the original user: [4](#0-3)

### Citations

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
