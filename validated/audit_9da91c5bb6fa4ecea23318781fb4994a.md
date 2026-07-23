## Analysis

The key analog to the external report's "premature registry update allows arbitrary data injection" bug class in Metric OMM is: **a guard checks the wrong identity (the router contract instead of the end user), allowing any user to bypass the swap allowlist by routing through `MetricOmmSimpleRouter`**.

Here is the exact call chain:

1. `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
2. When called via the router, `msg.sender` of the pool is the **router contract**, not the end user [2](#0-1) 
3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]` [3](#0-2) 

For router-mediated swaps to work on an allowlisted pool, the pool admin **must** add the router to the allowlist. Once the router is allowlisted, the per-user gate collapses: any user can bypass it by calling the router.

---

### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address. If the pool admin allowlists the router to enable router-mediated swaps, the per-user allowlist is silently voided: every user can bypass it by calling the router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
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

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct); `sender` is the router (wrong — should be the end user). The check therefore reads `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. In every case the pool sees `msg.sender = router`.

For router-mediated swaps to succeed on a pool guarded by `SwapAllowlistExtension`, the pool admin must add the router to the allowlist. Once that entry exists, the guard reduces to a single binary check — "is the router allowlisted?" — and every user who can reach the router can swap, regardless of their individual allowlist status.

### Impact Explanation
Any user can bypass the swap allowlist by calling `MetricOmmSimpleRouter` instead of the pool directly. Pools that rely on `SwapAllowlistExtension` to restrict swaps to KYC'd addresses, institutional counterparties, or trusted market makers are fully exposed to unauthorized swappers. Unauthorized swaps drain LP-owned token reserves at oracle-quoted prices, constituting a direct loss of LP principal. The allowlist guard — the only mechanism preventing this — is silently voided.

### Likelihood Explanation
The pool admin must allowlist the router for any router-mediated swap to succeed. This is a natural and expected configuration step: the router is a public, audited periphery contract and pool admins will routinely add it to enable standard user flows. The bypass requires no special privilege, no flash loan, and no unusual token behavior — only a call to a public router function.

### Recommendation
The `SwapAllowlistExtension` must gate the **end user**, not the immediate caller. Two sound approaches:

1. **Pass the real user in `extensionData`**: require the router to encode `msg.sender` in `extensionData` and have the extension verify it (e.g., via a signed permit or by trusting only the factory-registered router). The extension then checks `allowedSwapper[pool][decodedUser]`.
2. **Check `tx.origin` as a fallback**: when `sender` is a known router, fall back to `tx.origin`. This is safe only if the router is the sole trusted intermediary and reentrancy is excluded (which the pool's transient reentrancy guard provides).

The simplest safe fix is to remove the router from the allowlist and instead have the router forward the original caller's address as a verified `extensionData` field that the extension decodes and checks.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][alice] = true   (KYC'd user)
  pool admin: allowedSwapper[pool][router] = true  (to enable router swaps)

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

  Router calls pool.swap(...) → pool's msg.sender = router
  Pool calls extension.beforeSwap(sender=router, ...)
  Extension checks: allowedSwapper[pool][router] → true → passes
  Bob's swap executes, draining LP reserves at oracle price.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [3](#0-2) [1](#0-0) [4](#0-3)

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
