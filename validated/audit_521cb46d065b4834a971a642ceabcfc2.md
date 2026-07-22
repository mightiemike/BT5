### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, allowing any non-allowlisted user to bypass the swap allowlist by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension sees the router address, not the actual user. A pool admin who wants to allow specific users to use the router must allowlist the router itself — which then grants every user on-chain access to the curated pool, completely defeating the allowlist.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` of this call is the **router**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the router.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]`. [1](#0-0) [2](#0-1) [3](#0-2) 

The extension's guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. For router-mediated swaps, `sender = router`. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. [4](#0-3) 

The router passes `msg.sender` (the user) only into the transient callback context for payment settlement — it is never forwarded to the pool's `swap()` call as an identity argument. [5](#0-4) 

**The inescapable dilemma for the pool admin:**

| Admin action | Direct pool call | Router call |
|---|---|---|
| Allowlist only individual users (e.g., Alice) | Alice: allowed; Bob: blocked | Alice: blocked (router not allowlisted); Bob: blocked |
| Allowlist the router to enable router usage | Alice: blocked (not individually listed); Bob: allowed via router | **Everyone bypasses the allowlist** |

There is no configuration that simultaneously allows specific users to use the router and blocks others. Allowlisting the router — the only way to enable router-mediated swaps — opens the pool to all users.

---

### Impact Explanation

Any non-allowlisted user can swap on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). The pool admin's curation policy — intended to restrict trading to KYC'd, institutional, or otherwise vetted addresses — is completely nullified. Unauthorized users can drain LP liquidity at oracle-anchored prices, extract fees, or trade in pools they were explicitly excluded from.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed swap interface. Any pool that uses `SwapAllowlistExtension` and needs to support router-mediated swaps for any user must allowlist the router, triggering the bypass for all users. The bypass requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must check the **originating user**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through the router.** The router should forward `msg.sender` (the actual user) as a separate argument or via `extensionData`, and the extension should read it from there. This requires a protocol-level convention for how the originating user is communicated.

2. **Check `recipient` instead of `sender` when `sender` is a known router.** This is fragile and not recommended.

3. **Preferred: gate on `tx.origin` as a fallback identity.** While `tx.origin` has known limitations, for allowlist purposes it correctly identifies the EOA initiating the transaction regardless of router intermediation. The extension could check `allowedSwapper[pool][tx.origin]` when `sender` is not individually allowlisted.

4. **Structural fix:** Require the router to include the originating user in `extensionData` and have the extension decode and verify it. The pool admin allowlists individual users; the extension reads the user from `extensionData` and verifies the router signed or forwarded it correctly.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only Alice is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so Alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Bob (not allowlisted) calls the router directly
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
// This succeeds — extension sees sender=router, which IS allowlisted
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Bob successfully swapped on a pool he was explicitly excluded from
vm.stopPrank();
```

The `SwapAllowlistExtension` emits no revert because `allowedSwapper[pool][router] == true`, even though `allowedSwapper[pool][bob] == false`. [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
