### Title
`SwapAllowlistExtension` checks the router address instead of the actual end-user, allowing any unprivileged user to bypass the swap allowlist on curated pools — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router (required for any router-based swap to work) inadvertently opens the gate to every user on the internet.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is whatever the pool passes as the first argument to `_beforeSwap`. The pool passes its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`. [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(recipient, ...)`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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
``` [2](#0-1) 

The pool's `msg.sender` is the router, so the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`. The actual end-user's address is only stored in `params.recipient` (the output recipient) and in the router's transient callback context — neither of which the extension ever reads.

The `ExtensionCalling._beforeSwap` dispatcher passes `sender` straight through to every configured extension without modification: [3](#0-2) 

The same wrong-actor binding applies to the multi-hop `exactInput` path, where intermediate hops use `address(this)` (the router itself) as the payer: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swapping to a specific set of addresses. To allow those addresses to use the public router, the admin must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, the guard is completely open: any address on the internet can call `router.exactInputSingle()` and the extension will pass because it sees `sender = router ∈ allowlist`. The allowlist provides zero protection for router-mediated swaps. Unauthorized users can drain liquidity from a pool that was designed to be curated, causing direct loss of LP principal and breaking the core invariant that only approved counterparties trade against the pool.

---

### Likelihood Explanation

The router is the canonical user-facing entry point documented and deployed by the protocol. Any pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router address. This is not an edge case — it is the expected operational configuration. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged caller with no special setup.

---

### Recommendation

Pass the economically relevant actor — the end user — rather than the intermediate contract. Two options:

1. **Pool-level fix**: Have the pool pass `tx.origin` or a caller-supplied `swapper` field as `sender` to extensions. This is architecturally invasive.

2. **Extension-level fix (preferred)**: Change `SwapAllowlistExtension` to check `recipient` (the second parameter, which is the address that receives output tokens) instead of `sender`, or add a dedicated `swapper` field to the extension data that the router populates with `msg.sender` before calling the pool. The extension then reads and verifies that field.

3. **Router-level fix**: The router should forward the original `msg.sender` in `extensionData` in a standardized envelope, and the extension should decode and verify it (with the pool as the trusted source of the call, preventing spoofing).

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner explicitly passed to `addLiquidity`), which correctly identifies the economically relevant actor regardless of who the payer is. [5](#0-4) 

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — intending only Alice to swap.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` — so Alice can use the router.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` — pool's `msg.sender` is the router.
6. Pool calls `extension.beforeSwap(router, bob, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
8. Bob's swap executes against the curated pool despite never being allowlisted.

The allowlist is fully bypassed for any user who routes through the public `MetricOmmSimpleRouter`. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-85)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
