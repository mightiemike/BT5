Looking at the codebase, I need to trace the `sender` identity through the swap allowlist guard and the router path.

**Key files examined:**
- `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`
- `metric-periphery/contracts/MetricOmmSimpleRouter.sol`
- `metric-core/contracts/MetricOmmPool.sol` (swap → `_beforeSwap`)
- `metric-core/contracts/ExtensionCalling.sol` (`_beforeSwap` dispatcher)

**The pool always passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

**The extension gates on that `sender`:** [2](#0-1) 

**The router calls `pool.swap()` directly — making itself `msg.sender` to the pool:** [3](#0-2) 

---

### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is always `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract, not the user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user, completely neutralising the per-user allowlist.

### Finding Description

`MetricOmmPool.swap` hardcodes `msg.sender` as the `sender` argument forwarded to every `beforeSwap` extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry-point) calls `pool.swap()` directly, making the router the `msg.sender` to the pool:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

The extension therefore sees `sender = router`, not the user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

A pool admin who wants their allowlisted users to be able to use the standard periphery has only one option: `setAllowedToSwap(pool, router, true)`. The moment they do so, `allowedSwapper[pool][router] = true` and the check passes for **every** caller of the router, regardless of whether that caller is on the per-user allowlist.

There is no mechanism in the current design that lets a pool admin simultaneously:
- restrict swaps to a named set of users, **and**
- allow those users to use `MetricOmmSimpleRouter`.

### Impact Explanation

A curated pool that is supposed to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, whitelisted market-makers) can be accessed by any arbitrary user the moment the pool admin allowl

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
