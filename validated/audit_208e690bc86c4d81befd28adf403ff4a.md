Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so `sender` resolves to the router address. A pool admin who allowlists the router — the natural step to let permitted users access the router — inadvertently opens the gate to every user routing through that contract, including non-allowlisted ones.

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, binding `sender` to whoever called `pool.swap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to the configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the originating user's address: [4](#0-3) 

This makes `sender = router address` from the pool's perspective. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Exploit path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific users: `setAllowedToSwap(pool, alice, true)`.
2. Pool admin allowlists the router so `alice` can use it: `setAllowedToSwap(pool, router, true)`.
3. `charlie` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool records `msg.sender = router` as `sender`.
5. Pool calls `_beforeSwap(sender=router, ...)` → extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
6. `charlie`'s swap executes despite not being allowlisted.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` entry points on the router. [5](#0-4) 

The `setAllowedToSwap` setter provides no way to distinguish "allowlist the router on behalf of a user" from "allowlist the router as an unrestricted pass-through": [6](#0-5) 

## Impact Explanation

The access-control invariant the extension is designed to enforce — "only allowlisted addresses may swap on this pool" — is fully broken for any pool whose admin has allowlisted the router. Any non-allowlisted user can swap by routing through `MetricOmmSimpleRouter`. Pools using the allowlist for regulatory compliance, institutional-only access, or KYC gating are silently open to all router users. This is an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a pool-admin-configured guard, meeting the contest's allowed impact gate.

## Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router, which is the natural and expected action for any allowlisted pool that wants its permitted users to be able to use the standard periphery router. Once the router is allowlisted, the bypass is trivially reachable by any user with no special permissions or capital requirements beyond a normal swap. The condition is not adversarial — it arises from normal, expected admin behavior.

## Recommendation

The extension must check the originating user, not the direct caller. Two concrete options:

1. **Router-forwarded identity via `extensionData`**: Require the router to ABI-encode the originating `msg.sender` into `extensionData` and have the extension decode and check it. This requires a trusted router registry in the extension so that only known routers can assert a forwarded identity.
2. **Reject router-mediated swaps entirely**: Do not allowlist the router address; instead, require all allowlisted users to call `pool.swap()` directly. Document this constraint clearly.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists alice and the router
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// 3. charlie (not allowlisted) calls the router
vm.prank(charlie);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: charlie,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Swap succeeds — charlie bypassed the allowlist
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
