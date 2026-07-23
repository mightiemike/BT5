### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the original user. If the pool admin allowlists the router to support standard UX, every user on the network can bypass the curated allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`: [4](#0-3) 

So the extension receives `sender = address(MetricOmmSimpleRouter)` and evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. The same substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`.

The pool admin who wants allowlisted users to access the pool through the standard router UX must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every caller** of the router, regardless of whether that caller is on the intended allowlist.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional partners, or whitelisted market makers) is fully open to any user who routes through `MetricOmmSimpleRouter`. The allowlist provides zero protection against router-mediated swaps once the router is allowlisted. Unauthorized users can drain pool liquidity at oracle-quoted prices, causing direct loss to LPs who deposited under the assumption that only vetted counterparties could trade.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing swap interface for the protocol. Pool admins who want their allowlisted users to have a normal UX (rather than requiring direct pool calls) will naturally add the router to the allowlist. The documentation and extension design give no warning that doing so opens the pool to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router.

---

### Recommendation

The extension must gate on the **original economic actor**, not the direct pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and verifies it. This requires a coordinated convention between router and extension.
2. **Check `tx.origin` as a fallback for router calls**: When `sender` is a known router, fall back to `tx.origin`. This is simpler but has its own caveats with smart-contract wallets.
3. **Separate router-level allowlist**: Deploy a router wrapper that enforces the allowlist before forwarding to the pool, removing the need to allowlist the router in the extension at all.

The core invariant that must hold: the identity checked by `SwapAllowlistExtension` must be the same identity that the pool admin intended to gate, regardless of which supported public entrypoint reaches the pool.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, Alice, true)       // Alice is the intended gated user
  - Pool admin calls setAllowedToSwap(pool, router, true)      // Admin adds router to support normal UX

Attack:
  - Charlie (not allowlisted) calls:
      router.exactInputSingle({
          pool: pool,
          recipient: Charlie,
          zeroForOne: true,
          amountIn: X,
          ...
      })

  - pool.swap(recipient=Charlie, ...) is called with msg.sender = router
  - _beforeSwap(sender=router, ...) is dispatched
  - SwapAllowlistExtension checks: allowedSwapper[pool][router] == true  ✓
  - Swap executes; Charlie receives tokens at oracle price
  - Alice's allowlist entry is irrelevant; Charlie bypassed it entirely
```

The check at `SwapAllowlistExtension.sol:37` evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][Charlie]`, so the guard fails open for every user of the router. [5](#0-4) [6](#0-5) [7](#0-6)

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
