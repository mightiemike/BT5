### Title
SwapAllowlistExtension checks router address instead of actual user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-based swaps for legitimate users, any unprivileged user can bypass the allowlist by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (used as the mapping key) and `sender` is the first argument passed by the pool. The pool sets that argument to its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
)
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
``` [3](#0-2) 

The pool's `msg.sender` is therefore the **router address**, not the end user. The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

The same substitution occurs in every router entry point: `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (including the recursive callback hops in `_exactOutputIterateCallback`). [4](#0-3) 

For allowlisted users to use the router at all, the pool admin must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the guard passes for **every** caller of the router, regardless of whether the actual end user is on the allowlist.

---

### Impact Explanation

Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting a pool protected by `SwapAllowlistExtension`. If the router is allowlisted — which is required for legitimate allowlisted users to use the standard periphery — the allowlist guard is completely bypassed. Unauthorized users can trade in a curated pool, potentially draining LP funds or executing trades at prices the pool admin intended only for specific trusted counterparties. This is a direct loss of LP principal and a broken core pool access-control invariant.

---

### Likelihood Explanation

High. Pool admins who deploy a `SwapAllowlistExtension` and want their allowlisted users to be able to use the standard periphery router will naturally allowlist the router address. Once the router is allowlisted, any user can bypass the allowlist. The attack requires no special privileges, no unusual token behavior, and no complex setup — only a call to a public router function.

---

### Recommendation

The `SwapAllowlistExtension` must check the economically relevant actor (the end user), not the direct caller of `pool.swap()`. Concrete options:

1. **Extension-data forwarding**: Have the router encode the original `msg.sender` into `extensionData`; the extension decodes and checks that address. The pool admin must trust the router to forward it honestly, so the router should be a verified, immutable contract.
2. **Dedicated sender field**: Extend the hook interface to carry a separate `originator` address that the pool populates from a trusted forwarder context.
3. **No-router allowlist policy**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call `pool.swap()` directly. This is a severe UX restriction and is fragile.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as `extension1`.
2. Pool admin calls `setAllowedToSwap(pool, user1, true)` — only `user1` is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — required so `user1` can use the router.
4. Attacker (not `user1`, not otherwise allowlisted) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: pool,
       recipient: attacker,
       zeroForOne: true,
       amountIn: X,
       ...
   }));
   ```
5. Router calls `pool.swap(recipient=attacker, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Attacker's swap executes successfully — allowlist bypassed, LP funds exposed to unauthorized trading. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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
