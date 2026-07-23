All four cited code paths are confirmed against the actual production code:

- `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged into the extension call [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct pool caller [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender` to the pool; the originating user is stored only in transient callback context, never forwarded to the extension [4](#0-3) 

The inescapable dilemma is confirmed in code: there is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The finding is valid.

---

Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` Due to Wrong-Actor Binding — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which resolves to `msg.sender` at the pool level — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router (required for router-mediated swaps to function at all) inadvertently grants every user — including those explicitly excluded from the allowlist — the ability to bypass the gate by routing through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap` (metric-core/contracts/MetricOmmPool.sol L230–240). `ExtensionCalling._beforeSwap` forwards this value unchanged via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` (metric-core/contracts/ExtensionCalling.sol L160–176). `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct pool caller (metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37).

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly (metric-periphery/contracts/MetricOmmSimpleRouter.sol L72–80), making the router contract `msg.sender` to the pool. The originating user's address is stored only in transient callback context via `_setNextCallbackContext` for payment purposes and is never forwarded to the extension. The extension therefore checks whether the **router** is allowlisted, not the individual user.

This creates an inescapable dilemma: if the router is not allowlisted, all router-mediated swaps revert even for individually allowlisted users; if the router is allowlisted (the only way to support router swaps), every user bypasses the individual allowlist. No configuration resolves both requirements simultaneously.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified users, institutional participants, or permissioned beta testers) provides no effective access control for router-mediated swaps. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` targeting the restricted pool and execute swaps that the allowlist was intended to block. This is a direct admin-boundary break: the pool admin's access control policy is bypassed by an unprivileged path through a standard, publicly accessible periphery contract. The wrong value is the extension decision (`allowedSwapper[pool][sender]`) — it evaluates the router address instead of the originating user, producing an incorrect `true` result for any user routing through an allowlisted router.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any user aware of the router can trivially route around the allowlist with no special privileges, no flash loans, and no unusual token behavior. The pool admin has no mechanism to prevent this without breaking router functionality entirely for all users. Likelihood is high for any pool that both uses `SwapAllowlistExtension` and needs to support router-mediated swaps.

## Recommendation
The extension must gate the economically relevant actor — the originating user — not the intermediary. The cleanest fix is to modify `MetricOmmSimpleRouter` to include the originating `msg.sender` in a standardized field of `extensionData`, and modify `SwapAllowlistExtension.beforeSwap` to decode and check that address when present. Alternatively, document that pools using `SwapAllowlistExtension` must not allowlist the router and that allowlisted users must call the pool directly, accepting the usage restriction as the mitigation.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin must also allowlist the router for router swaps to work.
swapAllowlist.setAllowedToSwap(pool, address(router), true);   // required for router
swapAllowlist.setAllowedToSwap(pool, allowedUser, true);

// Attack: blockedUser (not individually allowlisted) routes through the router.
vm.startPrank(blockedUser);
token0.approve(address(router), type(uint256).max);

// Succeeds because the extension sees msg.sender = router, which IS allowlisted.
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: pool,
    recipient: blockedUser,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    tokenIn: token0,
    extensionData: ""
}));
// blockedUser successfully swapped on a pool they were explicitly excluded from.
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
