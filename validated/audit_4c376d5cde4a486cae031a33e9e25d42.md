### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool always passes `msg.sender` as `sender`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool boundary is the **router address**, not the actual end user. The extension therefore checks whether the router is allowlisted, not whether the real user is allowlisted. Any non-allowlisted user can bypass the guard by routing through the router.

---

### Finding Description

**Pool → Extension argument binding:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**The guard check:**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the first argument) as the identity being gated: [3](#0-2) 

**The router substitution:**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly. At that call site `msg.sender` is the router contract, so `sender` delivered to the extension is the router address, not the originating user: [4](#0-3) 

This creates an irreconcilable dilemma for any pool admin who deploys `SwapAllowlistExtension`:

| Admin configuration | Direct swap by allowlisted user | Router swap by allowlisted user | Router swap by **non-allowlisted** user |
|---|---|---|---|
| Router NOT allowlisted | ✅ passes | ❌ reverts | ❌ reverts |
| Router IS allowlisted | ✅ passes | ✅ passes | ✅ **passes — bypass** |

If the admin allowlists the router to enable router-mediated swaps for legitimate users, the allowlist is completely ineffective: any address can call the router and the extension sees only the router's address.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (KYC'd users, institutional market makers, whitelisted counterparties) is fully bypassed by any non-allowlisted user who calls `MetricOmmSimpleRouter`. The attacker receives output tokens directly as `recipient` while the router handles payment. The pool's access-control invariant is broken, and the pool's liquidity is exposed to unrestricted trading — a direct loss of LP principal through adverse selection or unauthorized price impact.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary production entry point for swaps. Any pool that (a) deploys `SwapAllowlistExtension` and (b) allowlists the router to support normal user flows is immediately vulnerable. The bypass requires no special privileges, no flash loans, and no unusual token behavior — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must verify the **originating user**, not the immediate caller of the pool. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the pool's forwarding of `extensionData` (already done correctly) and the router to honestly encode the user.

2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant identity is who receives the output. The extension already receives `recipient` as its second argument and could gate on that instead of (or in addition to) `sender`.

3. **Dedicated router-aware allowlist**: Maintain a separate allowlist entry for `(pool, router)` that is never set, and require the router to forward the real user identity via a signed payload in `extensionData`.

---

### Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension E
  - Admin calls E.setAllowedToSwap(P, router, true)   // to enable router-mediated swaps
  - Admin does NOT allowlist attacker: allowedSwapper[P][attacker] = false

Attack:
  1. Attacker calls MetricOmmSimpleRouter.exactInputSingle(
       pool = P,
       recipient = attacker,
       zeroForOne = true,
       amountIn = X,
       ...
     )
  2. Router calls P.swap(recipient=attacker, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[P][router] == true → passes
  5. Swap executes; attacker receives output tokens
  6. Pool calls router's metricOmmSwapCallback; router pulls tokens from attacker

Result: Non-allowlisted attacker completes a swap on a restricted pool.
        The allowlist guard is fully bypassed.
``` [3](#0-2) [5](#0-4) [6](#0-5)

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
