### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user to bypass per-user swap restrictions via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the immediate caller of `pool.swap()` — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. A pool admin who allowlists the router (a natural operational choice) inadvertently opens the pool to every user who routes through it, completely defeating the per-user curation the allowlist was meant to enforce.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as the first argument, which the pool sets to `msg.sender` of the `swap()` call:

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

The extension then checks:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) is used, it calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol – exactInputSingle()
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The router's own address becomes `sender` in the extension. The end user's identity (`msg.sender` of `exactInputSingle`) is stored only in transient payment context and is never forwarded to the extension.

**Concrete bypass path:**

| Step | Actor | Call | Extension sees |
|---|---|---|---|
| 1 | Pool admin | `setAllowedToSwap(pool, router, true)` | router allowlisted |
| 2 | Charlie (not allowlisted) | `router.exactInputSingle(pool, ...)` | `sender = router` → **allowed** |
| 3 | Charlie (not allowlisted) | `pool.swap(...)` directly | `sender = charlie` → **reverts** |

The pool admin cannot simultaneously allow allowlisted users to use the router and block non-allowlisted users from using the same router. Allowlisting the router is an all-or-nothing gate.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the position holder), so the deposit path does not share this flaw.

---

### Impact Explanation

Any user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter`. On a pool designed for specific counterparties (e.g., KYC'd traders, institutional LPs), unauthorized swaps can drain LP principal at oracle-quoted prices. The allowlist guard — the sole access-control mechanism for swap curation — is rendered ineffective the moment the router is allowlisted, which is the expected operational configuration for any pool that wants to support the official periphery.

---

### Likelihood Explanation

The trigger requires no special privilege. Any public user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The only precondition is that the pool admin has allowlisted the router address, which is the natural and expected setup for any pool that intends to support the official periphery. The bypass is therefore reachable on every curated pool that uses the router in production.

---

### Recommendation

Forward the original end-user identity through the router to the extension, or change the extension to check the economically relevant actor. Two options:

1. **Pass end-user identity via `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. This requires a coordinated change in both the router and the extension.

2. **Check `sender` as the end user at the pool level**: The pool could expose a separate "originator" field in the swap call that the router populates with `msg.sender`, and the extension checks that field instead of `sender`.

The simplest correct fix is to have the extension check the end user's address rather than the immediate caller of `pool.swap()`, mirroring how `DepositAllowlistExtension` correctly checks `owner` rather than `sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)
  pool admin calls setAllowedToSwap(pool, router, true)  ← natural operational step

Attack:
  charlie (not in allowlist) calls:
    router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true
    → swap executes for charlie

Verification:
  charlie calls pool.swap() directly:
    → extension checks allowedSwapper[pool][charlie] == false
    → reverts NotAllowedToSwap  ✓ (direct path blocked)

  charlie calls router.exactInputSingle():
    → extension checks allowedSwapper[pool][router] == true
    → swap executes  ✗ (router path bypasses per-user gate)
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
