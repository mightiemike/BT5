### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` parameter, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. If the pool admin adds the router to the allowlist (the natural step to let allowlisted users use the router), every non-allowlisted user can bypass the curation gate by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

So `msg.sender` to the pool is the **router**, and `sender` delivered to `beforeSwap` is the router's address — not the actual user. The allowlist check `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][actualUser]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the second parameter), which the liquidity adder always sets to the actual position owner regardless of who calls the adder: [5](#0-4) 

The two production extensions are therefore asymmetric: the deposit guard is actor-correct; the swap guard is actor-wrong when the router is in the path.

---

### Impact Explanation

A pool admin who wants allowlisted users to be able to use the router must add the router address to `allowedSwapper`. Once the router is allowlisted, **every** user — including those explicitly excluded from the allowlist — can call `router.exactInputSingle` and have the allowlist check pass, because the check sees the router, not the caller. The curation boundary the pool admin configured is silently voided. Any user can trade in a pool intended to be restricted, receiving the pool's favorable oracle-anchored pricing without authorization.

---

### Likelihood Explanation

The trigger is a normal, expected operational step: a pool admin enabling the router for a curated pool. The router is the canonical swap entrypoint documented in the protocol. There is no warning that adding the router to the allowlist opens the gate to all users. Any curated pool that wants to support router-based swaps will hit this condition. The attacker needs no special privilege — only the ability to call the public router.

---

### Recommendation

The swap allowlist must gate on the economic actor, not the immediate caller. Two options:

1. **Add a dedicated `swapper` / `payer` field to the swap interface.** The pool's `swap` function could accept an explicit `payer` address (analogous to `owner` in `addLiquidity`), set by the router to `msg.sender` of the router call, and forward it to `beforeSwap` as a distinct parameter. The extension then checks that field.

2. **Check `recipient` as a proxy when `sender` is a known router.** This is fragile and not recommended.

Option 1 mirrors the existing `addLiquidity(owner, ...)` pattern and makes the actor explicit at the pool interface level, so every extension automatically receives the correct identity.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (extension2, beforeSwap order)
  allowedUser  → allowedSwapper[pool][allowedUser]  = true
  router       → allowedSwapper[pool][router]        = true   ← admin adds router so allowedUser can use it

Attack:
  attacker (not in allowlist) calls:
    router.exactInputSingle({pool: pool, recipient: attacker, ...})

  router calls:
    pool.swap(attacker, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender to pool = router

  pool calls:
    _beforeSwap(sender=router, recipient=attacker, ...)

  SwapAllowlistExtension.beforeSwap:
    allowedSwapper[pool][router] == true  → check passes

  Result: attacker swaps successfully in a pool they are not allowlisted for.
```

The allowlist is completely bypassed. Any non-allowlisted user who routes through `MetricOmmSimpleRouter` trades freely in the curated pool.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
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
