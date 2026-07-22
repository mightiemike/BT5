### Title
SwapAllowlistExtension Sender Identity Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (the only way to permit any router-mediated swap), every user—including those explicitly excluded from the per-user allowlist—can bypass the restriction by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller), and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call. [1](#0-0) 

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` with itself as `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

The pool therefore passes `sender = router_address` to the extension. The allowlist check becomes `allowedSwapper[pool][router]`. If the router is allowlisted (which the admin must do to permit any router-mediated swap for legitimate users), the check passes for **every** user who routes through the router, regardless of whether that individual user is on the allowlist.

There is no mechanism in the router to forward the actual user's address to the extension. The `extensionData` bytes are passed through unchanged, but `SwapAllowlistExtension` does not decode them — it only reads the `sender` argument. [4](#0-3) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified counterparties, institutional market makers, or whitelisted protocols) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool admin cannot simultaneously:

1. Allow legitimate allowlisted users to swap via the router (requires allowlisting the router address), and
2. Block non-allowlisted users from swapping via the router (impossible once the router is allowlisted).

This breaks the core invariant the extension is designed to enforce. Unauthorized users gain the ability to trade against a pool that was explicitly configured to exclude them, which can violate regulatory requirements, expose LPs to unintended counterparties, or undermine pool-level access controls that LPs relied upon when depositing.

### Likelihood Explanation

The scenario is reachable by any unprivileged user. The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration for any pool that wants to support router-mediated swaps for its legitimate users. The admin has no way to know that doing so opens the gate to all users. No special timing, oracle state, or privileged access is required by the attacker.

### Recommendation

The `SwapAllowlistExtension` should not rely solely on the `sender` argument for identity when the sender may be a trusted intermediary. Two complementary fixes:

1. **Extension-side:** Decode the actual end-user address from `extensionData` when `sender` is a known router, and check that address against the allowlist. This requires a convention for how the router encodes the user.

2. **Router-side:** Have `MetricOmmSimpleRouter` encode `msg.sender` (the actual user) into `extensionData` before forwarding to the pool, so allowlist extensions can extract and verify the real initiator.

A simpler short-term mitigation: document that `SwapAllowlistExtension` cannot enforce per-user restrictions for router-mediated swaps, and advise pool admins to use `allowAllSwappers` only when they intend to open the pool to all users.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is the only allowed user)
  allowedSwapper[pool][router] = true  (admin adds router so alice can use it)

Attack:
  bob (not on allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

  Router calls pool.swap() with msg.sender = router
  Pool calls _beforeSwap(sender=router, ...)
  Extension checks allowedSwapper[pool][router] → true
  Swap succeeds for bob despite bob not being on the allowlist

Result:
  bob bypasses the per-user swap allowlist by routing through the public router.
``` [1](#0-0) [5](#0-4) [4](#0-3)

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
