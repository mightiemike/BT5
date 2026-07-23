### Title
Swap Allowlist Gates Router Address Instead of Actual Swapper, Enabling Full Bypass on Curated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The allowlist therefore gates the router's address rather than the actual economic actor. If the pool admin allowlists the router to enable router-mediated swaps, every user — including explicitly disallowed ones — can bypass the curated restriction.

---

### Finding Description

**Pool `swap()` passes `msg.sender` as `sender` to extensions:** [1](#0-0) 

`_beforeSwap` is called with `msg.sender` as the first argument. When the call originates from `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), `msg.sender` is the router contract address, not the end user.

**`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender`:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`:** [3](#0-2) 

When the call path is `user → router → pool → extension`, `sender` = router address. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Router calls `pool.swap()` directly with no mechanism to forward the original user:** [4](#0-3) 

There is no field in the pool's `swap()` signature for an "original caller" — the pool only sees `msg.sender` (the router).

**Contrast with `DepositAllowlistExtension`, which correctly gates `owner` (not `sender`):** [5](#0-4) 

Deposit allowlisting works correctly through the `MetricOmmPoolLiquidityAdder` because the pool passes `owner` (the position owner, supplied by the caller) as a separate argument, and the extension checks that field. No equivalent "intended swapper" field exists on the swap path.

---

### Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert for every user, including individually allowlisted ones — the primary user-facing interface is broken |
| Router **allowlisted** | Every user, including explicitly disallowed ones, can bypass the curated restriction by routing through `MetricOmmSimpleRouter` |

In the second (operationally necessary) case, the allowlist is completely defeated. Any user can call `router.exactInputSingle(...)` and trade on a pool that was designed to restrict access to specific counterparties. LP funds in a curated pool are exposed to unauthorized traders, which can cause direct loss of LP principal through adverse selection or manipulation that the allowlist was intended to prevent.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap interface; users are expected to route through it.
- For a curated pool to be usable at all via the router, the admin must allowlist the router — making the bypass trivially reachable by any address.
- No special privilege, flash loan, or multi-step setup is required: a single `exactInputSingle` call suffices.
- The bypass is permanent and structural; it cannot be patched by the pool admin without either breaking router access for everyone or removing the allowlist entirely.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **end user**, not the intermediary. Two sound approaches:

1. **Preferred — check `recipient` instead of `sender` for router flows, or add an `originalSender` field to the swap extension interface.** The pool's `swap()` already accepts a `recipient` parameter; a dedicated `originalSender` field (analogous to how `addLiquidity` separates `owner` from `msg.sender`) would let the router forward `msg.sender` explicitly and let the extension check it.

2. **Short-term — document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps**, and require curated pools to be accessed only via direct pool calls.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists only `trustedUser` and the router (to enable router swaps).
// `attacker` is NOT allowlisted.

// Direct swap by attacker → correctly reverts NotAllowedToSwap
vm.prank(attacker);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, true, 1000, 0, "", "");

// Router swap by attacker → SUCCEEDS because extension sees sender=router (allowlisted)
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Attacker receives token1 — allowlist fully bypassed.
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
