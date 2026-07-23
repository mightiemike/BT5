### Title
SwapAllowlistExtension Bypass via Router: `sender` Identity Mismatch Allows Any User to Swap on Restricted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is the caller, `sender` is the **router address**, not the end user. A pool admin who allowlists the router to enable normal router usage inadvertently opens the pool to every user, defeating the per-user allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` here is the pool (correct), and `sender` is whatever the pool passed as the first argument to `_beforeSwap`. In `MetricOmmPool.swap()`, that argument is always `msg.sender` of the pool call:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, not the end user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — all router paths call `pool.swap()` directly, making the router the `msg.sender` the pool sees. [4](#0-3) 

The result is that `allowedSwapper[pool][router]` is the only check that matters for any router-mediated swap. The extension has no visibility into the actual end user (`msg.sender` of the router call).

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| **No** | Allowlisted EOAs cannot use the router at all; they must call `pool.swap()` directly |
| **Yes** | Every user on the network can bypass the per-user allowlist by routing through the router |

If the admin allowlists the router (the natural choice for a production pool that expects users to interact via the standard periphery), the allowlist is completely ineffective: any non-allowlisted address can call `router.exactInputSingle(...)` and the extension will pass because `allowedSwapper[pool][router] == true`. The pool drains liquidity to unauthorized counterparties, violating the core invariant the allowlist was deployed to enforce.

This matches the **Allowlist path** impact gate: a disallowed swapper can still execute swaps and consume pool liquidity.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical, publicly deployed periphery contract. Any user can call it.
- A pool admin who wants their allowlisted users to have a normal UX **must** allowlist the router, triggering the bypass.
- No privileged access, no malicious setup, and no non-standard tokens are required. Any EOA can exploit this the moment the router is allowlisted.
- The only mitigating factor is that the pool admin must have chosen to allowlist the router, making this a semi-trusted trigger rather than a fully permissionless one.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **end user**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention and is fragile.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the end user, but this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Preferred — dedicated router that forwards the original caller**: The router stores the original `msg.sender` in transient storage (it already does this for the payer via `_setNextCallbackContext`) and exposes it. The extension reads it via a known interface. This is the cleanest separation and mirrors how the payer context is already handled.

Until fixed, pool admins should be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for UX

Attack (executed by bob, who is NOT allowlisted):
  4. bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
     → router calls pool.swap(bob, ...)  with msg.sender = router
     → pool calls extension.beforeSwap(sender=router, ...)
     → extension checks allowedSwapper[pool][router] == true  ✓ PASSES
     → bob receives output tokens from the restricted pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; bob drains liquidity from a pool he was never authorized to touch
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```
