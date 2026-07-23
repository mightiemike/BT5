### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the extension gates the router address rather than the actual end user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently opens the pool to every user, completely defeating the per-user allowlist.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap`, the guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the contract calling the extension), and `sender` is the first argument forwarded by the pool — which is `msg.sender` of `pool.swap()`. [1](#0-0) 

The pool's `swap()` function passes `msg.sender` as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value as the first argument to every configured extension: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) is called, the router itself calls `pool.swap()`: [4](#0-3) 

So `sender` received by the extension equals the **router address**, not the actual end user. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`.

A pool admin who wants to allow specific users to swap through the official router will naturally add the router to the allowlist:

```
swapExtension.setAllowedToSwap(pool, address(router), true);
```

Once the router is allowlisted, **any** address — including addresses the admin explicitly never allowlisted — can bypass the guard by routing through `MetricOmmSimpleRouter`. The extension sees `sender = router`, finds it allowlisted, and passes.

Note the contrast with `DepositAllowlistExtension`, which correctly gates the `owner` argument (the position owner, not the direct caller), making it robust to the liquidity adder: [5](#0-4) 

The swap extension has no equivalent forwarding of the true end user.

---

### Impact Explanation

**High.** A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a known set of counterparties (e.g., a private OTC pool, a pool gating MEV bots, or a pool restricted to KYC'd addresses) loses all access control the moment the router is allowlisted. Any unprivileged user can execute swaps, draining LP value at oracle-derived prices without the pool's intended restrictions. The loss is direct: tokens leave the pool at the oracle mid-price in exchange for the attacker's input, with no recourse.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the standard periphery swap path. Pool admins who want to allow specific users to use the router — rather than forcing them to call `pool.swap()` directly — will allowlist the router address. This is the natural, expected admin action. The design of the extension gives no indication that allowlisting the router opens the pool to all users; the admin has no way to simultaneously allow specific users through the router and block others, because the extension cannot distinguish end users behind the router.

---

### Recommendation

The extension must gate the actual end user, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Forward the real user in `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Check `sender` only for direct pool calls**: The extension can detect router-mediated calls (e.g., by checking whether `sender` is a known router) and require the real user to be encoded in `extensionData` in that case.
3. **Document the limitation clearly**: At minimum, the extension's NatSpec must warn that allowlisting any public intermediary (router, multicall) opens the pool to all users of that intermediary.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin allowlists the router:
       swapExtension.setAllowedToSwap(pool, address(router), true);
   (Intention: allow permitted users to use the router.)
3. Mallory (address never added to allowedSwapper) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool: pool,
           recipient: mallory,
           zeroForOne: true,
           amountIn: X,
           ...
       }));
4. Router calls pool.swap(); pool passes msg.sender = router as `sender` to _beforeSwap.
5. SwapAllowlistExtension.beforeSwap evaluates:
       allowedSwapper[pool][router] == true  →  check passes.
6. Mallory's swap executes on the curated pool.
   The per-user allowlist is completely bypassed.
``` [1](#0-0) [6](#0-5) [7](#0-6) [3](#0-2)

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
