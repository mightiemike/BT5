### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` parameter, which the pool sets to `msg.sender` (the direct pool caller). When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the actual user. A pool admin who allowlists the router to permit router-mediated swaps inadvertently opens the pool to every user, because any address can call the router and appear as the router to the extension.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput` / `exactOutput`).
2. The router calls `IMetricOmmPoolActions(pool).swap(recipient, ...)` — at this point `msg.sender` inside the pool is the **router address**.
3. `MetricOmmPool.swap` passes `msg.sender` (= router) as `sender` to `_beforeSwap`.
4. `ExtensionCalling._beforeSwap` encodes and forwards `sender` = router to every configured extension.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool and `sender` = **router**, not the original user.

If the pool admin allowlists the router address (a natural step to allow router-mediated swaps for any allowlisted user), the check passes for **every** caller of the router, regardless of whether that caller is on the allowlist.

The pool's `addLiquidity` correctly passes `owner` (the position beneficiary) to the deposit extension, so `DepositAllowlistExtension` is not affected by the same flaw. The swap path is uniquely vulnerable because the pool has no separate "original user" field — it only exposes `msg.sender` as `sender`. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted protocols) can be fully bypassed. Any unpermissioned user routes through `MetricOmmSimpleRouter`, the extension sees the router as the swapper, and if the router is allowlisted the guard passes. The attacker can then drain arbitrage value from the pool or trade against LP positions that were never meant to be exposed to public flow, causing direct loss of LP principal and fees. [4](#0-3) 

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is the expected operational step: without it, allowlisted users cannot use the router at all, so any pool that wants router-compatible allowlist enforcement must allowlist the router. The moment the admin does so, the bypass is open to every user. The router is a public, permissionless contract, so no special access is needed. [5](#0-4) [6](#0-5) 

---

### Recommendation

The extension must gate on the **original user**, not the direct pool caller. Two complementary fixes:

1. **Pass the original user through the extension interface.** The pool already receives `msg.sender` from the router; the router should forward the original user as a separate field (e.g., in `extensionData`) and the extension should decode and check that field.

2. **Alternatively, check `sender` only when `sender` is not a known router.** The extension could maintain a registry of trusted routers and, when `sender` is a router, require the router to attest the original user in `extensionData`.

The simplest safe fix is to have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that value when the direct `sender` is a recognized router. [1](#0-0) 

---

### Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension E.
  - Admin allowlists router R: E.setAllowedToSwap(P, router, true).
  - Alice (0xAlice) is NOT on the allowlist.

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: P, recipient: Alice, ...}).
  2. Router calls P.swap(Alice, ...) — msg.sender inside pool = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[P][router] → true.
  5. Swap executes. Alice receives output tokens.

Expected: revert NotAllowedToSwap (Alice is not allowlisted).
Actual:   swap succeeds because the router is allowlisted.
``` [3](#0-2) [7](#0-6) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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
