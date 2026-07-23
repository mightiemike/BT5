### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, breaking allowlist enforcement for all router-mediated swaps — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to gate swaps on curated pools to a configured set of allowed addresses. However, the `sender` argument it receives and checks is `msg.sender` of `pool.swap()` — which is the `MetricOmmSimpleRouter` contract address when users enter through the supported periphery path. The extension therefore checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. This produces two mutually exclusive failure modes: either allowlisted users are silently blocked from using the router, or the router is allowlisted and any user can bypass the curated gate entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router itself calls `pool.swap()`: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]` — a mapping entry that is almost certainly `false` for any pool that has configured a curated user allowlist.

The same mismatch applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput` (recursive callback hops), because in every case the pool's `swap()` is called by the router contract. [5](#0-4) 

---

### Impact Explanation

**Scenario A — allowlisted EOAs cannot use the router.**
Pool admin allowlists `alice` via `setAllowedToSwap(pool, alice, true)`. Alice calls `exactInputSingle` through the router. The extension sees `sender = router`, finds `allowedSwapper[pool][router] == false`, and reverts `NotAllowedToSwap`. Alice is permanently blocked from the supported periphery path despite being explicitly allowlisted. This is broken core pool functionality for every curated pool that uses the standard router.

**Scenario B — allowlisting the router defeats the entire gate.**
To unblock router users, the pool admin calls `setAllowedToSwap(pool, router, true)`. Now `allowedSwapper[pool][router] == true` for every swap that passes through the router, regardless of who the originating user is. Any address — including addresses the pool admin explicitly never allowlisted — can bypass the curated gate by routing through `MetricOmmSimpleRouter`. The allowlist invariant is fully broken.

Both scenarios are reachable by any public user interacting with a curated pool through the documented periphery path.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery contract explicitly listed in scope. `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool that deploys with `SwapAllowlistExtension` and expects users to swap through the router will immediately encounter this mismatch. No special preconditions, privileged setup, or unusual token behavior is required — a standard `exactInputSingle` call is sufficient to trigger either failure mode.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate pool caller. Two complementary fixes:

1. **Pass the originating user through the router.** The router already tracks `msg.sender` as the payer in transient storage. Extend the `extensionData` convention or add a dedicated field so the extension can recover the true initiator. The extension then decodes and checks that address instead of `sender`.

2. **Alternatively, check `sender` only when it is not a known router.** The extension could fall back to checking `recipient` or a user address embedded in `extensionData` when `sender` is a factory-registered pool-adjacent contract. This is more fragile but avoids changing the router ABI.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData` for allowlist-aware pools, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when present, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
//   pool configured with SwapAllowlistExtension (beforeSwap order)
//   alice is allowlisted:  extension.setAllowedToSwap(pool, alice, true)
//   router is NOT allowlisted

// Step 1: alice calls pool.swap() directly — succeeds
vm.prank(alice);
pool.swap(alice, true, 1000, type(uint128).max, "", "");
// ✓ passes: allowedSwapper[pool][alice] == true

// Step 2: alice calls router.exactInputSingle() — reverts
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: alice,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp + 1,
    tokenIn: token0,
    extensionData: ""
}));
// ✗ reverts NotAllowedToSwap:
//   pool passes msg.sender = router to _beforeSwap
//   extension checks allowedSwapper[pool][router] == false

// Step 3: admin allowlists the router to "fix" it
extension.setAllowedToSwap(address(pool), address(router), true);

// Step 4: bob (never allowlisted) bypasses the gate via router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp + 1,
    tokenIn: token0,
    extensionData: ""
}));
// ✓ passes: allowedSwapper[pool][router] == true — allowlist fully bypassed
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37: [6](#0-5) 

`sender` is `msg.sender` of `pool.swap()` — the router — not the originating user. The check must be rebound to the actual economic actor initiating the swap.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
