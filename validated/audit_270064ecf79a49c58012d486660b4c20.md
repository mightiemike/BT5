### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. Because `MetricOmmPool.swap` passes `msg.sender` (the immediate caller of the pool) as `sender`, any swap routed through `MetricOmmSimpleRouter` presents the router's address to the extension instead of the real end-user's address. A pool admin who allowlists the router to support the standard periphery flow inadvertently opens the allowlist to every user on-chain.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to every before-swap extension hook:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool):

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
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

The result is a structural dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Router allowlisted? | Effect |
|---|---|
| **No** | Allowlisted users cannot use `MetricOmmSimpleRouter` at all — broken core periphery flow |
| **Yes** | Every on-chain address can bypass the per-user allowlist by routing through the router |

---

### Impact Explanation

When the pool admin allowlists the router address (the natural fix for the broken-periphery case), the allowlist is completely neutralised. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool; the extension sees `sender = router` (allowlisted) and permits the swap. The curated pool's access control is silently voided, allowing unauthorised parties to trade against pool liquidity. This constitutes a direct policy bypass on a production guard with fund-impacting consequences (unauthorised price-taking against LP capital in a pool designed to restrict counterparties).

---

### Likelihood Explanation

The trigger is a single, publicly callable function on the canonical periphery contract. No privileged setup beyond the pool admin allowlisting the router is required, and allowlisting the router is the only way to restore the standard periphery flow for legitimate users. The router is a shared, immutable contract, so allowlisting it is a one-time admin action that permanently opens the bypass. Likelihood is **Medium** (requires one admin action that is the natural remediation for the broken-periphery symptom).

---

### Recommendation

The extension must gate the economically relevant actor — the end-user — not the intermediate router. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Add a dedicated `swapOnBehalf` entry-point on the pool** that accepts an explicit `swapper` address and enforces `msg.sender == authorisedRouter` before forwarding `swapper` to extension hooks. Extensions then check the explicit `swapper` field.

Either approach ensures the allowlist always gates the address that controls the economic action, regardless of which supported periphery path reaches the pool.

---

### Proof of Concept

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Admin observes that Alice cannot use `MetricOmmSimpleRouter` (swap reverts because `allowedSwapper[pool][router] == false`).
4. Admin calls `setAllowedToSwap(pool, router, true)` to restore the standard periphery flow.
5. Attacker (Bob, not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle` with `pool` as the target.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router] == true` → **passes**.
7. Bob's swap executes against the curated pool's liquidity with no allowlist enforcement. [5](#0-4) [1](#0-0) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-41)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
