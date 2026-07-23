### Title
`SwapAllowlistExtension` Gates Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. Any user can bypass a per-user swap allowlist by calling the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates two mutually exclusive broken states for any pool that configures `SwapAllowlistExtension` with a per-user allowlist:

1. **Router not allowlisted**: Allowlisted users cannot use the router at all — their swaps revert because the extension sees the router address, which is not on the list. Core swap functionality is broken for the intended user set.

2. **Router allowlisted** (the only way to let allowlisted users use the router): The allowlist is completely bypassed — any non-allowlisted user can call `router.exactInputSingle()` and the extension passes because `allowedSwapper[pool][router] == true`. The per-user gate is nullified.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, and to any intermediate hop in a multi-hop path where the router itself is `msg.sender` of `pool.swap()`. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted protocols) cannot achieve that restriction when the `MetricOmmSimpleRouter` is in scope. To allow their allowlisted users to use the router, the admin must allowlist the router address. Once the router is allowlisted, any unprivileged user can call `router.exactInputSingle()` and execute swaps against the restricted pool, draining LP assets at oracle-quoted prices without authorization. This is a direct loss of LP principal because the pool's access control — the only mechanism preventing unauthorized swaps — is fully bypassed.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery contract deployed alongside the router. Any pool that configures it with a per-user allowlist and also needs the router to be usable (the common case) is immediately vulnerable. The attacker needs no special privilege: calling `router.exactInputSingle()` with a valid pool address and token approval is sufficient. The trigger is fully unprivileged and reachable from any EOA.

---

### Recommendation

The extension must identify the **economic actor** (the user who initiated the transaction), not the **direct caller** of `pool.swap()`. Two options:

**Option A — Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it. This requires the extension to trust the encoding, which is fragile unless the pool enforces the format.

**Option B — Check `recipient` instead of `sender`**: For swap allowlists the economically relevant identity is the recipient of output tokens. Replace the `sender` check with a `recipient` check in `beforeSwap`. This is already available as the second argument.

**Option C (preferred) — Allowlist check in the router, not the extension**: Move the per-user gate into a router-level modifier that checks `msg.sender` before calling the pool. The extension then only needs to verify the router itself is authorized.

The simplest safe fix consistent with the existing interface is Option B:

```solidity
function beforeSwap(
    address,          // sender (router) — ignored
    address recipient,
    bool, int128, uint128, uint256, uint128, uint128, bytes calldata
) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists only `trustedUser` and the router (so trustedUser can use the router).
// `attacker` is NOT on the allowlist.

// 1. Admin setup
vm.prank(poolAdmin);
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
vm.prank(poolAdmin);
swapAllowlist.setAllowedToSwap(address(pool), trustedUser, true);

// 2. Attacker (not allowlisted) bypasses the guard via the router
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);

// This succeeds — extension sees sender=router, router is allowlisted, check passes
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    tokenOut:        address(token1),
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    recipient:       attacker,
    deadline:        block.timestamp + 1,
    priceLimitX64:   0,
    extensionData:   ""
}));
// Attacker receives token1 from the restricted pool — allowlist bypassed.
vm.stopPrank();
```

The root cause is in `SwapAllowlistExtension.beforeSwap` checking `sender` (the router) rather than the actual user, and in `MetricOmmPool.swap` passing `msg.sender` (the router) as `sender` to the extension hook. [3](#0-2) [1](#0-0) [6](#0-5)

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
