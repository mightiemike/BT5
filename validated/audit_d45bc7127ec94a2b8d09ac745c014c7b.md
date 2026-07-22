### Title
`SwapAllowlistExtension` checks the router's address instead of the originating user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the first argument forwarded by the pool — the pool's own `msg.sender` at the time `swap()` was called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router address to enable router-based swaps, every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` field in the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` uses that `sender` argument — together with `msg.sender` (the pool) — to look up the allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point the pool's `msg.sender` is the router, so `sender` delivered to the extension is the router's address, not the originating user's address. The allowlist check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an impossible dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Allowlist specific user addresses only | Those users can only swap **directly**; router-mediated swaps revert for everyone, including allowlisted users |
| Allowlist the router address | **Every** user on the network can swap through the router, defeating the allowlist entirely |

There is no configuration that restricts router-based swaps to a specific subset of users.

The analog to the Babylon covenant-key bug is exact: just as `parseCovenantKeysFromSlice` strips the format byte and then `schnorr.ParsePubKey` silently re-adds the wrong one (always `0x02`), `SwapAllowlistExtension` strips the originating user identity and silently substitutes the router's identity — the wrong principal — for the allowlist check.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a restricted venue (e.g., institutional-only, KYC-gated, or partner-only). Once the pool admin allowlists the router to give allowlisted users a better UX, the gate is open to the entire public. Any user can execute swaps against the pool's LP liquidity, exposing LPs to adversarial flow (e.g., informed order flow, stop-loss triggering, or bin-cursor manipulation) that the allowlist was designed to prevent. This is a broken core pool functionality — the access-control invariant the pool admin configured is silently violated — with direct exposure of LP principal to unintended counterparties.

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps in the periphery. A pool admin who deploys a swap-allowlisted pool and wants allowlisted users to benefit from slippage protection, multi-hop routing, or deadline enforcement will naturally allowlist the router. The bypass requires no special privilege: any EOA can call `MetricOmmSimpleRouter.exactInputSingle` with the restricted pool as the target. The trigger is a single public transaction.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the extension interface.** The pool already receives `msg.sender`; for router calls the router should forward the original `msg.sender` as a separate field (e.g., inside `extensionData`) so the extension can verify it. Alternatively, the pool could expose a `swapOnBehalf(address user, ...)` entry point that records the true originator in a transient slot, analogous to how `MetricOmmPoolLiquidityAdder` stores the payer.

2. **Short-term mitigation.** Document that allowlisting the router address is equivalent to `allowAllSwappers = true`, and add a check in `setAllowedToSwap` that warns (or reverts) when the router address is supplied.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  pool admin allowlists router R: E.setAllowedToSwap(P, router, true)
  user Alice (address A) is NOT in the allowlist
  user Bob   (address B) IS  in the allowlist

Direct swap by Alice (blocked as intended):
  Alice calls P.swap(...) directly
  → beforeSwap receives sender = Alice
  → allowedSwapper[P][Alice] == false → revert NotAllowedToSwap ✓

Router swap by Alice (bypass):
  Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  → router calls P.swap(recipient, ...)
  → pool's msg.sender = router
  → beforeSwap receives sender = router
  → allowedSwapper[P][router] == true → passes ✗

Alice successfully swaps against a pool she was explicitly denied access to.
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
