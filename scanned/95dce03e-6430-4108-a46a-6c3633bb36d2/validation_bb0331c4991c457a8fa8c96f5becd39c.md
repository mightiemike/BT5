### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` passed to the extension is the router address — not the original user. If the pool admin allowlists the router as a "trusted intermediary," every user bypasses the per-user gate. If the router is not allowlisted, allowlisted users cannot swap through the router at all, breaking core pool functionality.

---

### Finding Description

**Call chain — direct swap (correct):**
```
user → pool.swap()
  pool: msg.sender = user
  _beforeSwap(sender=user, ...)
  extension: allowedSwapper[pool][user]  ✓ correct identity
```

**Call chain — router swap (broken):**
```
user → MetricOmmSimpleRouter.exactInputSingle()
  router → pool.swap(recipient, ...)
    pool: msg.sender = router
    _beforeSwap(sender=router, ...)
    extension: allowedSwapper[pool][router]  ✗ wrong identity
```

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router, not the original user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original `msg.sender` into the extension layer: [4](#0-3) 

The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

**Scenario A — Router is allowlisted (bypass):** A pool admin who wants to allow users to swap through the router must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every user regardless of their individual allowlist status. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the curated pool, completely defeating the access control. This is a direct loss of curation integrity and allows unauthorized users to drain LP value from a pool designed to restrict counterparties.

**Scenario B — Router is not allowlisted (broken functionality):** Allowlisted users who attempt to swap through the router are blocked because the router address is not in the allowlist. The primary user-facing swap interface is unusable for the pool's intended participants, constituting broken core pool functionality.

Both outcomes are fund-impacting: Scenario A enables unauthorized swaps against LP principal; Scenario B prevents legitimate swaps and may strand LP positions.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary public swap entrypoint for EOA users.
- Pool admins who deploy allowlisted pools will naturally expect users to use the router; allowlisting the router is the obvious operational step, directly triggering Scenario A.
- No privileged access is required. Any user can call the router.
- The bypass is deterministic and repeatable in every block.

---

### Recommendation

The extension must check the economically relevant actor — the original user — not the intermediary contract. Two approaches:

1. **Pass original sender via `extensionData`:** The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and verifies it. This requires a coordinated convention between router and extension.

2. **Check `sender` as the pool's direct caller only when the caller is not a known router:** The extension maintains a registry of trusted routers and, when `sender` is a router, reads the original user from a standardized field in `extensionData`.

3. **Simplest fix — document and enforce direct-pool-only usage:** If the allowlist is intended only for direct pool calls, the pool should reject calls from non-allowlisted intermediaries at the pool level, not the extension level. However, this breaks composability.

The cleanest production fix is option 1: the router encodes `abi.encode(msg.sender)` as a prefix in `extensionData` for allowlisted pools, and the extension decodes the original caller from `extensionData` when `sender` is a known router.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
// router is NOT allowlisted

// Step 1: allowedUser swaps directly — succeeds
vm.prank(allowedUser);
pool.swap(recipient, zeroForOne, amount, priceLimit, "", "");

// Step 2: bannedUser routes through router — extension checks allowedSwapper[pool][router]
// router is not allowlisted → reverts (Scenario B: allowedUser also blocked via router)

// Now admin allowlists the router to "enable router usage":
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Step 3: bannedUser routes through router — extension checks allowedSwapper[pool][router] == true
// bannedUser bypasses the allowlist (Scenario A)
vm.prank(bannedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bannedUser,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Succeeds — bannedUser has swapped on a curated pool they were never meant to access
```

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner, an explicit parameter), not on `sender` (the pool's caller): [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
