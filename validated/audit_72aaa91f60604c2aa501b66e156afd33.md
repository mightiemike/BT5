### Title
`SwapAllowlistExtension` gates the router's address instead of the originating user, allowing any actor to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` where `sender` is the immediate `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. The extension therefore checks whether the router is allowlisted, not whether the actual user is allowlisted. Any non-allowlisted user can bypass the curation policy by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim as the `sender` argument to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` value against the per-pool allowlist:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point), the router calls `pool.swap()` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(params.recipient, ...);
``` [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router address**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the router is the direct caller of `pool.swap()`. [5](#0-4) 

**Concrete bypass path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only `userA`.
2. To let `userA` use the router, the admin must also allowlist the router address.
3. Once the router is allowlisted, **any** address — including `userB` who is explicitly not allowlisted — can call `router.exactInputSingle({pool: curatedPool, ...})`. The pool sees `msg.sender = router`, the extension passes, and `userB` swaps successfully.
4. Alternatively, if the admin does **not** allowlist the router, even `userA` cannot use the router, making the router unusable for any allowlisted user on that pool.

The allowlist is therefore either fully bypassed (router allowlisted) or fully broken for router users (router not allowlisted). There is no configuration that enforces per-user restrictions through the router.

---

### Impact Explanation

**Direct loss of curation policy / High.** Pools that deploy `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise curated counterparties have that restriction silently nullified for any user who routes through `MetricOmmSimpleRouter`. The router is a public, permissionless contract. Any non-allowlisted user can execute swaps against the pool, draining LP value at the oracle-anchored price, which the pool admin intended to reserve for specific counterparties only.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the canonical user-facing swap interface. Any user who discovers the allowlist restriction on a direct `pool.swap()` call can trivially re-route through the router. No privileged access, special tokens, or unusual setup is required — only a standard `exactInputSingle` call.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **In the router:** store `msg.sender` (the originating user) in transient storage alongside the callback context, and expose it so the pool can forward it as the true `sender` to extensions. This mirrors how the router already stores the payer address for callback settlement.

2. **In `SwapAllowlistExtension`:** check `recipient` (the address that receives output tokens) as a proxy for the economic actor, or require the pool to pass an authenticated originator field. The `recipient` is already available as the second argument to `beforeSwap` and is harder to spoof than `sender` when routing.

The cleanest fix is option 1: the router writes the originating `msg.sender` into a transient slot, the pool reads it via a standardized interface, and passes it as `sender` to extensions. This preserves the existing extension ABI while correcting the identity binding.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
//   - curatedPool has SwapAllowlistExtension configured
//   - pool admin calls: swapExt.setAllowedToSwap(curatedPool, router, true)
//     (necessary so that allowlisted users can use the router)
//   - pool admin calls: swapExt.setAllowedToSwap(curatedPool, alice, true)
//   - bob is NOT allowlisted

// Direct call — correctly blocked:
//   vm.prank(bob);
//   curatedPool.swap(...);  // reverts NotAllowedToSwap ✓

// Router call — bypass:
//   vm.prank(bob);
//   router.exactInputSingle(ExactInputSingleParams({
//       pool: curatedPool,
//       ...
//   }));
//   // pool.swap() is called with msg.sender = router
//   // extension checks allowedSwapper[curatedPool][router] == true
//   // swap succeeds — bob bypasses the allowlist ✗
```

The pool's `_beforeSwap` receives `sender = address(router)`. The extension evaluates `allowedSwapper[curatedPool][router]`, which is `true` (set by the admin to enable router usage for `alice`). Bob's swap executes at the oracle price against LP capital the admin intended to restrict. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
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
