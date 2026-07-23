Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` evaluates `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any non-allowlisted user can bypass a curated pool's allowlist by calling the public router instead of calling `pool.swap()` directly.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim to every configured extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` value against the per-pool allowlist, where `msg.sender` is the pool address and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` inside `pool.swap()`: [4](#0-3) 

The same substitution occurs in `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165) — in every case the router is the direct caller of `pool.swap()`. [5](#0-4) 

**Exploit path:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only `userA`.
2. To let `userA` use the router, the admin must also allowlist the router address (`allowedSwapper[pool][router] = true`).
3. Once the router is allowlisted, any address — including `userB` who is not allowlisted — calls `router.exactInputSingle({pool: curatedPool, ...})`. The pool sees `msg.sender = router`, the extension checks `allowedSwapper[pool][router] == true`, and `userB`'s swap succeeds.
4. Alternatively, if the admin does not allowlist the router, even `userA` cannot use the router, making the router entirely unusable for allowlisted users on that pool.

No existing guard prevents this: the extension has no mechanism to distinguish the originating user from the immediate caller, and the router stores `msg.sender` only in transient storage for callback payment purposes — it is never forwarded to the pool as an authenticated originator. [6](#0-5) 

## Impact Explanation
Direct loss of curation policy / High. Pools deploying `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties have that restriction silently nullified for any user routing through `MetricOmmSimpleRouter`. The router is a public, permissionless contract. Non-allowlisted users can execute swaps against the pool at the oracle-anchored price, draining LP value that the pool admin intended to reserve for specific counterparties only.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the canonical user-facing swap interface. Any user who encounters a `NotAllowedToSwap` revert on a direct `pool.swap()` call can trivially re-route through the router. No privileged access, special tokens, or unusual setup is required — only a standard `exactInputSingle` call to the public router.

## Recommendation
The `sender` argument passed to `beforeSwap` must represent the originating user, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **In the router:** store `msg.sender` (the originating user) in transient storage alongside the existing callback context, and expose it via a standardized interface so the pool can forward it as the true `sender` to extensions. The router already stores the payer address in transient storage for callback settlement — the same pattern applies here. [6](#0-5) 

2. **In `SwapAllowlistExtension`:** as a defense-in-depth measure, check `recipient` (the address receiving output tokens) as a proxy for the economic actor, since `recipient` is already available as the second argument to `beforeSwap` and is harder to spoof than `sender` when routing. [7](#0-6) 

## Proof of Concept
```solidity
// Setup:
//   - curatedPool has SwapAllowlistExtension configured
//   - pool admin: swapExt.setAllowedToSwap(curatedPool, router, true)
//     (required so allowlisted users can use the router)
//   - pool admin: swapExt.setAllowedToSwap(curatedPool, alice, true)
//   - bob is NOT allowlisted

// Direct call — correctly blocked:
vm.prank(bob);
curatedPool.swap(...);  // reverts NotAllowedToSwap ✓

// Router call — bypass:
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: curatedPool,
    recipient: bob,
    ...
}));
// pool.swap() is called with msg.sender = router
// extension checks allowedSwapper[curatedPool][router] == true
// swap succeeds — bob bypasses the allowlist ✗
```

Inside `pool.swap()`, `msg.sender` is the router address. `_beforeSwap` receives `sender = address(router)`. The extension evaluates `allowedSwapper[curatedPool][router]`, which is `true` (set by the admin to enable router usage for `alice`). Bob's swap executes at the oracle price against LP capital the admin intended to restrict. [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
