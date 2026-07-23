I have enough information to synthesize the finding. Let me trace the full call path before writing the output.

**Key trace:**

1. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the first argument passed by the pool. [1](#0-0) 

2. `ExtensionCalling._beforeSwap()` passes `sender` (the pool's `msg.sender`) as the first argument to the extension. [2](#0-1) 

3. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap(params.recipient, ...)` — the pool's `msg.sender` is the **router**, not the end user. [3](#0-2) 

4. The test confirms the pattern: the allowlist is set on `callers[0]` (the direct pool caller), not `users[0]` (the EOA). [4](#0-3) 

---

### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the curated-pool allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the router address, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for curated users simultaneously opens the gate to every unprivileged user who calls the same router.

### Finding Description
`SwapAllowlistExtension.beforeSwap()` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (the extension's caller). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap()`, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle()` calls:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

The pool receives `msg.sender = router`. It forwards `sender = router` to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates an inescapable dilemma for pool admins:

- **Option A — allowlist individual EOAs only**: allowlisted users cannot swap through the router (the router is not in the allowlist), breaking the primary user-facing path.
- **Option B — also allowlist the router**: every unprivileged user can bypass the allowlist by routing through `MetricOmmSimpleRouter`, because the extension sees `sender = router` for all of them.

The same structural flaw applies to `exactInput` (multi-hop) and `exactOutput` / `exactOutputSingle`, all of which call `pool.swap()` with `msg.sender = router`. [5](#0-4) [6](#0-5) 

### Impact Explanation
Any unprivileged user can execute swaps on a pool whose `SwapAllowlistExtension` was configured to restrict trading to a curated set (e.g., KYC-verified addresses, institutional counterparties). Once the router is allowlisted — the only way to let curated users trade via the standard periphery — the allowlist is completely ineffective. Unauthorized traders can drain LP-owned liquidity at oracle-derived prices, causing direct loss of LP principal. This is a broken core pool functionality with direct fund-loss consequence above Sherlock thresholds.

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the canonical swap interface documented and expected by end users. A pool admin who deploys a curated pool and then discovers that allowlisted users cannot use the router will naturally add the router to the allowlist as the obvious fix. At that point the bypass is immediately exploitable by any address. No privileged access, no special token, and no malicious setup is required — a standard `exactInputSingle` call suffices.

### Recommendation
The `sender` argument forwarded to `beforeSwap` must represent the economic actor, not the intermediary contract. Two sound approaches:

1. **Pass the real user through `extensionData`**: require the router to encode `msg.sender` (the end user) into `extensionData`; the extension decodes and verifies it. The pool's `msg.sender` (the router) must be separately verified as an approved relay.
2. **Add a dedicated `swapFor(address onBehalfOf, ...)` entry-point on the pool**: the pool records `onBehalfOf` as the authoritative actor and forwards it as `sender` to extensions, while still using `msg.sender` for callback settlement.

Either approach mirrors the GnosisBase fix: verify *both* the intermediary identity (`msg.sender` / chain ID) *and* the originating actor (`messageSender()` / end user) before granting access.

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls swapExtension.setAllowedToSwap(pool, alice, true).
   → alice is the only allowlisted swapper.

Broken path (Option A — no router allowlist)
────────────────────────────────────────────
3. alice calls router.exactInputSingle({pool: pool, ...}).
   router → pool.swap(recipient, ...)   [msg.sender = router]
   pool   → extension.beforeSwap(sender=router, ...)
   extension: allowedSwapper[pool][router] == false  → REVERT
   alice cannot use the router despite being allowlisted.

Bypass path (Option B — admin adds router to fix alice's UX)
─────────────────────────────────────────────────────────────
4. Admin calls swapExtension.setAllowedToSwap(pool, router, true).
5. bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
   router → pool.swap(recipient, ...)   [msg.sender = router]
   pool   → extension.beforeSwap(sender=router, ...)
   extension: allowedSwapper[pool][router] == true   → PASSES
   bob executes a swap in the curated pool, bypassing the allowlist.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
