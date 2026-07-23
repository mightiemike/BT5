Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, making the per-user allowlist completely bypassable by any unprivileged caller who routes through the public router.

## Finding Description
**Confirmed call chain in production code:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

This creates an irreconcilable dilemma for the pool admin:
- **Router not allowlisted:** `allowedSwapper[pool][router] == false` → every legitimate user's router call reverts, even if the user is individually allowlisted.
- **Router allowlisted:** `allowedSwapper[pool][router] == true` → every address on the planet can bypass the per-user allowlist by routing through the public router.

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` from the router's address. [5](#0-4) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is explicitly curated — the admin restricts which addresses may trade. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and the extension will check `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the only way to let legitimate users use it), the guard is completely open to all users. This is a direct policy bypass enabling unauthorized swaps against LP capital in a pool designed to be permissioned. This meets the "Admin-boundary break" criterion: an unprivileged path bypasses a pool admin access control.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical public swap entrypoint. Any user who reads the interface will naturally use it. The bypass requires no special knowledge, no privileged role, and no unusual token behavior — just calling the public router with the curated pool address. The attack is repeatable and costless beyond gas.

## Recommendation
The extension must gate on the original user, not the intermediate router. Two sound approaches:

1. **Pass the original user through `extensionData`:** Have the router encode `msg.sender` in `extensionData` and have the extension decode and verify it. This requires a coordinated extension+router design.
2. **Add an `originalSender` field to the hook signature:** The pool could pass both `msg.sender` (the direct caller) and an optional `originalSender` that the router populates via `extensionData`, letting the extension choose which to gate.
3. **Document incompatibility:** If the above is not implemented, explicitly document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and must only be used with direct pool calls.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to let legitimate users use router
  attacker = address not in allowedSwapper[pool]

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: curated_pool,
      recipient: attacker,
      ...
  })
  → router calls pool.swap(attacker, ...)          // msg.sender = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes, attacker receives output tokens
  → allowlist completely bypassed
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
