### Title
SwapAllowlistExtension gates the router address instead of the end user, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which is the immediate caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the allowlist entirely by calling through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes this value as the first positional argument in the ABI call to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the immediate caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with itself as `msg.sender`: [4](#0-3) 

The call chain is:

```
User → Router.exactInputSingle()
     → Pool.swap(msg.sender = Router)
     → Extension.beforeSwap(sender = Router)
     → allowedSwapper[pool][Router]  ← checked, NOT the user
```

The allowlist is keyed by `allowedSwapper[pool][swapper]` and is intended to gate individual users. However, the extension sees only the router's address. The actual end user's identity is permanently lost before the guard runs.

**Two failure modes arise:**

1. **Allowlist bypass (primary impact):** The pool admin must allowlist the router to permit any router-mediated swap. Once `allowedSwapper[pool][router] = true`, every user — including those the admin explicitly excluded — can bypass the allowlist by routing through the public `MetricOmmSimpleRouter`. The guard is rendered inoperative for all router-mediated swaps.

2. **Denial of service (secondary impact):** If the admin does not allowlist the router, individually allowlisted users who attempt to swap via the router are blocked, even though they are authorized. The router is the standard periphery entry point, so this breaks the expected user flow.

---

### Impact Explanation

The allowlist bypass is the critical path. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` or `exactInput` targeting a restricted pool. If the router is allowlisted (the only way to enable router-mediated swaps), the `SwapAllowlistExtension` guard is completely bypassed for all users. Unauthorized swappers gain full access to a pool that the admin intended to restrict, violating the admin-boundary invariant and potentially exposing LP funds to unwanted counterparties or price manipulation from actors the pool was designed to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard swap entry point for end users. Any pool deploying `SwapAllowlistExtension` to restrict access will face this issue the moment the router is allowlisted. The trigger requires no special privileges — any public user can call the router. The condition (router allowlisted) is the natural operational state for any pool that wants to support both allowlist enforcement and router-mediated swaps.

---

### Recommendation

The extension must validate the **economic actor**, not the immediate caller. Two approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and validates it. This requires a trusted router or a signed attestation.

2. **Check `tx.origin` as a fallback identity:** Only viable if the pool is not intended to be called from other contracts, and carries its own risks.

3. **Preferred — validate against a router-aware allowlist:** The extension checks whether `sender` is an approved router, and if so, requires the actual user identity to be passed and validated via `extensionData`. The router must be modified to forward `msg.sender` in `extensionData`.

The simplest safe fix is to have the router always encode the original caller in `extensionData` and have the extension decode and gate on that value when the immediate `sender` is a known router.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Admin must also allowlist the router to allow router-mediated swaps.
allowlistExt.setAllowedToSwap(pool, alice, true);
allowlistExt.setAllowedToSwap(pool, address(router), true); // required for router to work

// Attack: `bob` (not allowlisted) swaps through the router.
// bob → router.exactInputSingle({pool: pool, ...})
//      → pool.swap(msg.sender = router)
//      → extension.beforeSwap(sender = router)
//      → allowedSwapper[pool][router] == true  ← passes!
// bob successfully swaps on a pool he was supposed to be excluded from.
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// No revert — allowlist bypassed.
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
