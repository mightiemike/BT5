### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. If the router is allowlisted (which is necessary for allowlisted users to use the router), any non-allowlisted user can bypass the guard by routing through the router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(...)` (or any other router entry point).
2. The router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`.
3. Inside `MetricOmmPool.swap`, `_beforeSwap(msg.sender, ...)` is called — here `msg.sender` is the **router**, not the original user. [1](#0-0) 

4. `ExtensionCalling._beforeSwap` forwards `sender` (= router address) to the extension. [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is the router address. [3](#0-2) 

**The structural trap:**

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | ❌ Blocked (unusable) | ❌ Blocked |
| Yes | ✅ Passes | ✅ Passes (bypass!) |

A pool admin who wants allowlisted users to be able to use the router **must** allowlist the router address. But doing so grants every user — allowlisted or not — the ability to bypass the guard by routing through `MetricOmmSimpleRouter`. There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly checks `owner` (the position owner, which is the economically relevant actor), not `sender` (the caller/payer). The swap extension checks `sender`, which is the wrong actor when an intermediary router is involved. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd institutions, whitelisted market makers) can be bypassed by any user routing through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against restricted LP positions, defeating the curation policy. This constitutes a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a configured access guard, allowing unauthorized actors to trade against LP funds that were only meant to be accessible to specific counterparties.

---

### Likelihood Explanation

- `SwapAllowlistExtension` is a production extension in the periphery, designed for real deployment.
- `MetricOmmSimpleRouter` is the primary user-facing swap interface.
- Any pool admin who deploys a swap-allowlisted pool and also allowlists the router (a natural operational step to allow their users to use the standard router) creates the bypass condition.
- The attacker needs no special privileges — only the ability to call `MetricOmmSimpleRouter`.

---

### Recommendation

The extension must gate on the **original user**, not the intermediary. Two approaches:

1. **Check `sender` only for direct pool calls; require the router to forward the original user identity** — e.g., encode the original `msg.sender` in `extensionData` and have the extension verify it (requires router cooperation and is fragile).

2. **Preferred: gate on `recipient` or require the router to pass the original caller as `sender`** — modify `MetricOmmSimpleRouter` to pass the original `msg.sender` as a verified field in `extensionData`, and update `SwapAllowlistExtension` to decode and check that field when present.

3. **Simplest: document that the router cannot be used with swap-allowlisted pools**, and add a revert in the extension if `msg.sender` (the pool) has the router as `sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is allowlisted)
  - allowedSwapper[pool][router] = true  (router allowlisted so alice can use it)
  - bob is NOT allowlisted

Direct swap by bob:
  bob → pool.swap(...)
  extension sees sender = bob → NOT in allowlist → REVERT ✓

Router swap by bob (bypass):
  bob → router.exactInputSingle({pool: pool, ...})
  router → pool.swap(...)
  extension sees sender = router → IN allowlist → PASSES ✗

Result: bob executes a swap against restricted LP positions.
``` [5](#0-4) [3](#0-2)

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
