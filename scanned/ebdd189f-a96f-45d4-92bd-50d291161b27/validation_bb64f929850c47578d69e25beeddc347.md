### Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the original user. If the router is allowlisted (the natural operational setup), every user — including those explicitly excluded — can bypass the allowlist by calling any of the router's public `exact*` functions.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)
              [msg.sender = router]
         → MetricOmmPool._beforeSwap(msg.sender=router, ...)
         → ExtensionCalling._callExtensionsInOrder(...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks: allowedSwapper[pool][router]
```

**Pool `swap` passes its own `msg.sender` as `sender` to the extension:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards that value unchanged:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the router:** [3](#0-2) 

**The router calls the pool directly with no user-identity forwarding:** [4](#0-3) 

The router is a public, permissionless contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. The pool only ever sees `msg.sender = router`. The extension therefore evaluates the router's allowlist status, not the original user's.

Two failure modes exist:

1. **Bypass (primary impact):** Pool admin allowlists the router as a trusted periphery entry point. Every non-allowlisted user can now swap on the curated pool by routing through the router — the allowlist is completely ineffective.
2. **Lockout (secondary impact):** Pool admin does NOT allowlist the router. Allowlisted users cannot use the router at all; they must call the pool directly, breaking the expected UX and integration surface.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, protocol-owned bots, or whitelisted market makers) loses that restriction entirely for any user who routes through the public router. Non-allowlisted users can execute real swaps, moving pool reserves and extracting output tokens at oracle-derived prices. This is a direct loss of the curation policy and enables unauthorized fund flows out of the pool.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented user-facing entry point for swaps.
- Pool admins who configure `SwapAllowlistExtension` will naturally also allowlist the router so that their permitted users can trade through the normal UI/SDK path.
- No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call from any EOA suffices.
- The bypass is silent: the extension sees a passing check (`allowedSwapper[pool][router] == true`) and emits no anomalous event.

---

### Recommendation

The extension must check the **original user**, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`:** The `sender` argument already carries `msg.sender` of the pool's `swap` call. When the pool is called by a trusted router, the router should forward the original user's address. Add a router-level forwarding mechanism (e.g., encode the original `msg.sender` in `extensionData` and have the extension decode and verify it when `sender` is a known router), or:

2. **Preferred — check `tx.origin` as a fallback only when `sender` is a known router:** This is fragile; the cleaner fix is to have the router pass the original caller's address in `extensionData` and have the extension decode it when `sender` matches a registered router address.

3. **Alternatively:** Document that the router must never be allowlisted on pools using `SwapAllowlistExtension`, and that allowlisted users must call the pool directly. This is a severe UX restriction and is not a real mitigation.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Router is allowlisted so that allowedUser can trade through it.
extension.setAllowedToSwap(pool, router, true);       // router allowlisted
extension.setAllowedToSwap(pool, allowedUser, true);  // intended gated user
// attacker is NOT allowlisted

// Direct swap from attacker → reverts (correct)
vm.prank(attacker);
pool.swap(attacker, true, 1000, 0, "", ""); // reverts NotAllowedToSwap ✓

// Router swap from attacker → succeeds (bypass)
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Pool sees msg.sender = router → extension checks allowedSwapper[pool][router] = true → passes
// Attacker receives token1 output despite not being allowlisted ✗
```

The pool's `swap` function passes `msg.sender` (the router) as `sender` to `_beforeSwap`, so the extension evaluates the router's allowlist entry rather than the attacker's. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
