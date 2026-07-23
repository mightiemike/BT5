### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. For allowlisted users to trade through the router, the pool admin must allowlist the router address. Once the router is allowlisted, every user — including those explicitly excluded from the allowlist — can bypass the guard by routing through the same public contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value received above: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The router stores the original caller only in its internal transient callback context (for payment settlement) and never surfaces it to the pool or the extension: [5](#0-4) 

The result is a forced dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Cannot use the router at all | Correctly blocked |
| **Allowlist the router** | Can use the router | **Also pass — bypass achieved** |

There is no configuration that simultaneously permits allowlisted users to trade through the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

Any user excluded from the per-pool allowlist can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting a curated pool. If the pool admin has allowlisted the router (the only way to let legitimate users trade through the router), the extension sees `sender = router` and passes the check. The non-allowlisted user executes a full swap against the pool's liquidity, draining LP value at oracle-derived prices without the curation policy being enforced. This is a direct loss of the pool's intended access-control invariant and constitutes unauthorized extraction of LP assets on a pool designed to restrict trading to specific counterparties.

---

### Likelihood Explanation

The router is the primary user-facing swap entrypoint for the protocol. Any pool admin who wants allowlisted users to be able to use the standard periphery must allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` with a valid pool address. The attack is reachable on every production pool that uses `SwapAllowlistExtension` and has the router allowlisted.

---

### Recommendation

The extension must gate on the **economic actor** (the end user), not the **call-chain intermediary** (the router). Two complementary fixes:

1. **Pass the originating user through the router.** Add an optional `originator` field to the `extensionData` payload that the router populates with `msg.sender`. The extension reads and verifies this field when `sender` is a known router address. This requires the extension to maintain a registry of trusted routers.

2. **Alternatively, check `sender` only when it is not a trusted router; otherwise check the originator from `extensionData`.** This keeps the extension self-contained and avoids requiring router changes.

A simpler but more restrictive fix is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the pool configuration level (e.g., revert in `initialize` if the pool has a router allowlisted).

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is the intended user
  allowedSwapper[pool][router] = true   // required so alice can use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool:      pool,
      recipient: bob,
      zeroForOne: true,
      amountIn:  X,
      ...
    })

  router calls pool.swap(bob, true, X, ...)
    → msg.sender to pool = router
    → pool calls _beforeSwap(router, bob, ...)
    → extension checks allowedSwapper[pool][router] → TRUE
    → swap executes, bob receives output tokens

Result:
  bob, who is explicitly excluded from the allowlist, completes a swap
  against the pool's LP liquidity. The allowlist guard is silently bypassed.
```

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
