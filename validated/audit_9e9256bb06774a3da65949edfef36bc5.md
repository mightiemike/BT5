### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct `msg.sender` of the pool's `swap` call. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the **router address**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user of the public router bypasses the per-user allowlist, exposing LP funds to unauthorized counterparties.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is the value the pool received as `msg.sender` when `swap` was called on it.

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to the hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-L240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap
  recipient,
  ...
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any router function) calls `pool.swap`, the pool's `msg.sender` is the **router**, not the end user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-L80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← original user address is NOT forwarded
  );
```

The router does not forward the original caller's address to the pool or to the extension. The extension therefore evaluates:

```
allowedSwapper[pool][router]
```

not

```
allowedSwapper[pool][end_user]
```

A pool admin who wants to allow their vetted users to trade through the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, **any** address that calls `MetricOmmSimpleRouter` can swap against the restricted pool, regardless of whether that address is individually allowlisted.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production mechanism for restricting pool access to specific counterparties (e.g., institutional LPs, KYC'd users, or protocol-controlled addresses). When the router is allowlisted, the guard collapses to `allowAllSwappers[pool] = true` in effect: every public user of the router can trade. Unauthorized traders can extract value from LP positions that were provisioned under the assumption of a restricted counterparty set, causing direct loss of LP principal through adverse selection.

---

### Likelihood Explanation

The trigger is a natural and expected admin action: allowlisting the router so that vetted users can access the pool through the standard periphery. The admin has no on-chain signal that this opens the pool to all router users. The `SwapAllowlistExtension` documentation says it "Gates `swap` by swapper address," which implies per-user control, but the router collapses that control to a single bit. Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router is fully exposed.

---

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two sound approaches:

1. **Require the original user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a convention between router and extension but keeps the pool interface unchanged.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the end user; however, this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Document the invariant explicitly**: If the design intent is that the router is always an open relay, document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and provide a separate per-user gate that the router enforces before calling the pool.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin allowlists router: setAllowedToSwap(pool, router, true)
  alice (allowlisted individually) and bob (NOT allowlisted) both exist

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → checks allowedSwapper[pool][router] == true  ✓
    → swap proceeds; bob trades against the restricted pool

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; bob bypasses the allowlist
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
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
