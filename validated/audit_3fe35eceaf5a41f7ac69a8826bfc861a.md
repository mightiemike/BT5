Audit Report

## Title
SwapAllowlistExtension checks router address instead of original user, allowing allowlist bypass through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, so `sender` = router address. The extension therefore checks whether the router is allowlisted, not whether the original user is allowlisted. Any user can bypass a curated pool's per-user allowlist by calling `router.exactInputSingle` instead of calling `pool.swap` directly.

## Finding Description
`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with no forwarding of the original caller's address: [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` (= router address) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that router address as `sender` and dispatches it to the extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router. The original transaction initiator's address is never read: [4](#0-3) 

If the pool admin allowlists the router (the only mechanism to enable router-based swaps on a curated pool), every address on the internet can swap through the router regardless of individual allowlist status. If the admin does not allowlist the router, individually allowlisted users cannot use the router at all. Both failure modes are structural and require no malicious setup.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers) provides no actual restriction for router-mediated swaps. Any address can call `router.exactInputSingle` and execute a swap on the pool. This is a direct access-control failure: unauthorized parties can trade against the pool's liquidity, causing unauthorized price impact, fee extraction, or violation of compliance requirements the allowlist was meant to enforce. The exact wrong value is the `sender` identity passed to `allowedSwapper[pool][sender]` — it is the router address instead of the economically acting user.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery swap path. Pool admins who want to allow router-based swaps on a curated pool must allowlist the router address — there is no other mechanism. Once the router is allowlisted, the bypass is unconditional and requires no special setup by the attacker beyond calling the public router. The likelihood is high for any curated pool that intends to support router-based swaps.

## Recommendation
The `sender` passed to extension hooks must represent the original transaction initiator, not the intermediate caller. Two viable approaches:

1. **Router forwards original caller via `extensionData`** — `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` and `SwapAllowlistExtension` reads it from there, verifying the pool is the caller via `msg.sender`. This preserves composability and smart-contract wallet support.
2. **Allowlist check at pool level** — add a `SWAP_ALLOWLIST_PROVIDER` check in `MetricOmmPool.swap` that reads the original `msg.sender` before any extension dispatch, similar to how `NotAllowedToSwap` is documented in the pool interface. This is the cleanest fix consistent with the existing architecture.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension active
// 2. Admin allowlists only the router: swapExt.setAllowedToSwap(pool, address(router), true)
// 3. Unlisted user calls router directly
vm.prank(unlistedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    ...
}));
// Swap succeeds — beforeSwap received sender=router (allowlisted), not unlistedUser

// 4. Same unlisted user calls pool directly
vm.prank(unlistedUser);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(unlistedUser, true, 1000, 0, "", "");
// Reverts correctly — beforeSwap received sender=unlistedUser (not allowlisted)
```

The asymmetry proves the bypass: the same user is blocked on the direct path but succeeds through the router.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
