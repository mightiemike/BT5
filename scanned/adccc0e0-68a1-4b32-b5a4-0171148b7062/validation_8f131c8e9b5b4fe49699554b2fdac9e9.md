### Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the actual user, allowing non-allowlisted users to bypass the swap guard via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the originating user. The allowlist therefore gates on the router's address rather than the actual swapper. If a pool admin allowlists the router to enable router-mediated swaps for their curated users, every unprivileged user can bypass the allowlist by routing through the same router.

---

### Finding Description

**Call path:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, ..., extensionData)   [msg.sender = router]
             → ExtensionCalling._beforeSwap(sender=router, recipient=user, ...)
                 → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                     → allowedSwapper[pool][router]   ← wrong actor checked
```

In `MetricOmmSimpleRouter.exactInputSingle` the pool is called directly: [1](#0-0) 

The pool receives `msg.sender = router` and forwards it as `sender` to `_beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`msg.sender` here is the pool; `sender` is the **router**, not the originating user. The allowlist lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` — the LP position owner explicitly passed through the call chain — rather than `sender`: [4](#0-3) 

The asymmetry is the root cause: the deposit guard keys on the economically relevant actor (`owner`); the swap guard keys on the immediate caller (`sender`), which is the router when periphery is used.

---

### Impact Explanation

A pool admin who wants allowlisted users to be able to swap through the standard router must allowlist the router address itself. Once `allowedSwapper[pool][router] = true`, **any** address — including non-allowlisted users — can call `router.exactInputSingle()` and pass the guard, because the extension only sees `sender = router`. The allowlist is completely neutralised for all router-mediated swaps, allowing unprivileged users to trade on a curated pool. This constitutes a broken core guard with direct fund-impact potential (LP exposure to unintended counterparties, compliance bypass, or price-protection bypass on pools that rely on the allowlist as a safety boundary).

---

### Likelihood Explanation

Likelihood is **medium**. The bypass requires the pool admin to have allowlisted the router — a natural and expected operational step for any pool that wants its curated users to access the standard periphery. The router is a public, permissionless contract, so once the router is allowlisted the bypass is reachable by any address with no further preconditions.

---

### Recommendation

Gate the swap allowlist on the **originating user** rather than the immediate caller. Two options:

1. **Pass the original user through the call chain.** The router already knows `msg.sender`; it can forward it as an additional field in `extensionData` or as a dedicated parameter. The extension then reads the true initiator from that field.

2. **Check `recipient` instead of `sender`.** For single-hop swaps the recipient is the user; however this breaks for multi-hop flows where intermediate recipients are the router itself.

The cleanest fix is option 1: the router encodes the originating user in `extensionData`, and `SwapAllowlistExtension` decodes and checks that address when `sender` is a known router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Call `swapExtension.setAllowedToSwap(pool, router, true)` — the admin allowlists the router so that their curated users can swap via periphery.
3. As an **un-allowlisted** EOA, call `router.exactInputSingle({pool: pool, tokenIn: ..., tokenOut: ..., zeroForOne: true, ...})`.
4. The pool calls `_beforeSwap(sender=router, ...)`. The extension evaluates `allowedSwapper[pool][router] == true` and returns the success selector.
5. The swap executes successfully for the non-allowlisted user — the guard is bypassed.

Contrast: a direct call `pool.swap(...)` from the same non-allowlisted EOA correctly reverts with `NotAllowedToSwap` because `allowedSwapper[pool][user] == false`. [3](#0-2) [5](#0-4) [2](#0-1)

### Citations

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
