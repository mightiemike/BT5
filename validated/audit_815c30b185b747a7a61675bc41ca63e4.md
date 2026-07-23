Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the end user's address, making the allowlist bypassable or unusable via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the direct caller of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. This means the pool admin's per-user allowlist is checked against the router address: if the router is allowlisted, every user bypasses the gate; if it is not, every allowlisted user is blocked from using the router.

## Finding Description

**Call chain:**

1. End user calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ..., params.extensionData)` — `msg.sender` of this call is the router.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the router address.
4. `ExtensionCalling._beforeSwap` encodes `sender` (= router) and calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. Inside `beforeSwap`, `msg.sender` is the pool and `sender` is the router:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Root cause:** `MetricOmmPool.swap()` passes `msg.sender` (the immediate caller) as `sender` to the extension. The router is the immediate caller, so the extension never sees the original user.

**Existing guards are insufficient:** The router's `_requireExpectedCallbackCaller` and `_requireFactoryPool` only validate the callback path; they do not forward the original user identity to the pool or extension.

**Two broken scenarios:**

- **Allowlist bypass:** Pool admin allowlists the router so that legitimate users can swap through it. Any non-allowlisted user can also call the router and the check passes, completely defeating the allowlist.
- **Allowlist lockout:** Pool admin allowlists individual user addresses (not the router). Every allowlisted user is blocked from swapping through the router because `allowedSwapper[pool][router]` is `false`.

## Impact Explanation
The `SwapAllowlistExtension` is a core access-control hook. Its failure means either (a) any unprivileged user can trade in a pool the admin intended to restrict, or (b) the pool becomes unusable via the standard router for all allowlisted users. Both outcomes break the admin-boundary invariant and constitute broken core pool functionality. Severity: **High** — the allowlist provides zero protection against router-mediated swaps when the router is allowlisted, and the pool admin has no way to simultaneously allow router access and restrict individual users.

## Likelihood Explanation
Exploitation requires only that the pool admin has allowlisted the router address (the natural configuration to let users trade). Any unprivileged address can then call `exactInputSingle` or `exactInput` on the router targeting the restricted pool. No special privileges, flash loans, or oracle manipulation are needed. The condition is met in every standard deployment that uses both `SwapAllowlistExtension` and `MetricOmmSimpleRouter`.

## Recommendation
Pass the original user identity through the extension mechanism. Options:

1. **Encode original sender in `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and `SwapAllowlistExtension` decodes and checks it. This requires the extension to trust the router, which can be enforced by checking that `sender` (the pool caller) is a known factory router.
2. **Add a `swapper` field to the swap interface:** Introduce an explicit `swapper` parameter (distinct from `sender`) that the pool passes to extensions, allowing routers to supply the originating user address.
3. **Check `recipient` as a proxy:** If the pool admin's intent is to restrict who receives tokens, check `recipient` instead of `sender`. This is a partial fix only.

## Proof of Concept

```solidity
// Setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin calls setAllowedToSwap(pool, address(router), true)
//    (necessary for any user to swap via router)
// 3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

// Attack:
// attacker (non-allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    extensionData: "",
    deadline: block.timestamp
}));
// pool.swap() is called with msg.sender = router
// beforeSwap receives sender = router (allowlisted) → check passes
// attacker swaps successfully despite not being on the allowlist
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-241)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
