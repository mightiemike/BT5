Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of the pool. When `MetricOmmSimpleRouter` intermediates a swap, `msg.sender` to the pool is the router contract, not the end user. Any pool admin who allowlists the router to enable approved users to trade simultaneously grants unrestricted swap access to every address on the network.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the immediate pool caller: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool. The original end-user address is stored only in transient storage for the payment callback and is never forwarded to the pool or extension: [4](#0-3) 

Therefore, for every router-mediated swap, the allowlist evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`. Once the router is allowlisted — a necessary step for any approved user to trade through it — the check passes for every caller of the router regardless of individual approval status.

## Impact Explanation
A pool admin deploying a curated pool with `SwapAllowlistExtension` (e.g., for KYC-restricted trading) must allowlist the router for approved users to access the pool through the standard periphery. The moment the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every caller of `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`. The allowlist is completely neutralized for all router-mediated swaps. Any unapproved address can trade on the curated pool and receive oracle-priced output that the pool was designed to restrict. This constitutes broken core pool functionality — the pool's primary access-control mechanism is rendered inoperative.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point deployed alongside the protocol. Pool admins configuring a swap allowlist will naturally allowlist the router to serve their approved users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA calls `exactInputSingle` on the router pointing at the curated pool. The precondition (router allowlisted) is an expected and routine admin action, not an edge case.

## Recommendation
The extension must gate on the economically relevant actor — the originating end user — not the intermediate contract. The preferred fix is for `MetricOmmPool.swap` to accept an explicit `originator` parameter that the router populates with its `msg.sender` before calling the pool, and for `_beforeSwap` / `SwapAllowlistExtension.beforeSwap` to check that value. Alternatively, the router can encode the original `msg.sender` inside `extensionData` and the extension can decode and verify it, though this requires off-chain coordination and is more fragile.

## Proof of Concept
```
Setup:
  1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  2. Pool admin: setAllowedToSwap(pool, alice, true)   // alice is KYC-approved
  3. Pool admin: setAllowedToSwap(pool, router, true)  // required for alice to use the router

Attack:
  4. Bob (never individually approved) calls:
       router.exactInputSingle({
           pool:      <curated pool>,
           recipient: bob,
           zeroForOne: true,
           amountIn:  X,
           ...
       })

  5. Router calls pool.swap(bob, true, X, ...) with msg.sender = router.
  6. Pool calls _beforeSwap(router, bob, ...).
  7. ExtensionCalling calls extension.beforeSwap(router, bob, ...) with msg.sender = pool.
  8. Extension evaluates: allowedSwapper[pool][router] == true → passes.
  9. Bob receives oracle-priced token1 output. His address was never checked.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
