### Title
`SwapAllowlistExtension` Checks Router Address as Swapper, Allowing Any User to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool receives the **router** as `msg.sender` and forwards it as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged address can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
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

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly, making the pool's `msg.sender` the **router contract**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key) and `sender` is the router address. The extension never sees the actual user's address.

**Attack path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific users (`alice`, `bob`).
2. To let those users trade via the standard periphery, the admin also allowlists the router: `setAllowedToSwap(pool, router, true)`.
3. Any unprivileged address (`charlie`) calls `MetricOmmSimpleRouter.exactInputSingle(...)`. The pool receives `msg.sender = router`, the extension checks `allowedSwapper[pool][router] == true`, and the swap succeeds — the allowlist is fully bypassed.

The same bypass applies to all four router entry points: `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`. In the multi-hop `exactInput` case, intermediate hops also call `pool.swap` from the router, so every hop on an allowlisted pool is bypassed identically.

---

### Impact Explanation

Any user can trade on a pool that the admin intended to restrict to a curated set of addresses. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, whitelist-only LP pools), this allows unauthorized parties to extract value from the pool at oracle-derived prices, drain one-sided liquidity, or interact with pools whose terms they are not entitled to. The loss is direct and repeatable: every swap by an unauthorized user that the allowlist was meant to block represents a policy violation with real fund movement.

---

### Likelihood Explanation

The trigger is fully unprivileged — any EOA or contract can call `MetricOmmSimpleRouter`. The only precondition is that the pool admin has allowlisted the router (a natural and expected operational step to make the pool usable via the standard periphery). No special role, no malicious setup, and no non-standard token behavior is required. The router is a public, deployed contract.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economically relevant actor** — the end user — not the intermediary router. Two complementary fixes:

1. **Pass the original user through the router.** Add a `swapper` field to the swap parameters or use a transient-storage pattern (analogous to how `MetricOmmSwapRouterBase` already stores the payer) so the router records `msg.sender` before calling the pool, and the pool or extension can read it.

2. **Check `tx.origin` as a fallback only if the sender is a known router.** This is weaker but simpler: if `sender` is a registered router, the extension resolves the actual user from a router-provided transient slot.

The cleanest fix is option 1: the router stores the original caller in transient storage before calling `pool.swap`, and the pool forwards it as a separate `originator` field to extension hooks, which the allowlist checks instead of (or in addition to) `sender`.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; alice is allowlisted, charlie is not.
// Admin also allowlists the router so alice can use it.
extension.setAllowedToSwap(pool, alice, true);
extension.setAllowedToSwap(pool, address(router), true); // ← required for alice to use router

// Charlie (not allowlisted) bypasses the guard via the router:
vm.prank(charlie);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         token0,
        recipient:       charlie,
        deadline:        block.timestamp + 1,
        amountIn:        1000,
        amountOutMinimum: 0,
        zeroForOne:      true,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// ✓ swap succeeds — extension saw sender=router (allowlisted), never checked charlie
```

**Root cause location:** [1](#0-0) [2](#0-1) [3](#0-2)

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
