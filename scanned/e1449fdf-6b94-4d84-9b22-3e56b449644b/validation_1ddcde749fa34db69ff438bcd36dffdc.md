### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of the `swap` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router, not the actual user. If the pool admin allowlists the router (a necessary step to support router-mediated swaps for any allowlisted user), every unpermissioned user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool): [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the end user: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot swap through the router at all |
| Allowlist the router | **Every** user, including non-allowlisted ones, can bypass the gate |

There is no configuration that simultaneously supports router-mediated swaps for allowlisted users while blocking non-allowlisted users.

The same identity collapse occurs for every router entry point: `exactInput` intermediate hops use `address(this)` as recipient and the router as caller, and `exactOutput`'s recursive callback also calls `pool.swap` from the router context. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

Any user who is not on the allowlist can execute swaps on a restricted pool simply by calling `MetricOmmSimpleRouter.exactInputSingle`. The pool admin's intent to gate swap access to specific addresses is completely defeated. Unauthorized swaps move the pool's bin cursor, consume LP liquidity, and extract output tokens — direct loss of LP assets and broken core pool functionality.

---

### Likelihood Explanation

The bypass is reachable by any unprivileged user. The only prerequisite is that the pool admin allowlists the router, which is the natural and expected action for any pool that is meant to be usable through the protocol's own periphery. A pool admin who does not allowlist the router renders the pool unusable via the router for everyone, including their own allowlisted users. The incentive to allowlist the router is therefore strong, making the bypass highly likely to be reachable in practice.

---

### Recommendation

The `sender` forwarded to extensions must represent the economic actor, not the intermediary. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` (or a dedicated field) so extensions can decode and check the real user.
2. **Extension-side**: `SwapAllowlistExtension` should decode the real user from `extensionData` when `sender` is a known router, or the pool interface should carry an explicit `originator` field through the hook chain.

Until fixed, pool admins should be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin calls:
     extension.setAllowedToSwap(pool, alice, true);      // allowlist Alice
     extension.setAllowedToSwap(pool, router, true);     // allow router so Alice can use it
3. Bob (not allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: bob, ...})
4. Inside pool.swap(), msg.sender == router.
5. Extension checks allowedSwapper[pool][router] == true → passes.
6. Bob's swap executes on the restricted pool, extracting output tokens.
7. Direct call by Bob (pool.swap() with msg.sender == bob) would have reverted:
     allowedSwapper[pool][bob] == false → NotAllowedToSwap.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
