### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` (the direct caller). When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router address so that legitimate users can reach the pool through the router, every unprivileged user can bypass the per-user allowlist by routing through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (enforced by `onlyPool`), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-L176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

The pool populates `sender` with its own `msg.sender` — confirmed by the interface error comment ("Swap allowlist rejected `msg.sender`") and by the integration test that allowlists `address(callers[0])` (the direct caller contract), not the EOA behind it.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-L80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is therefore the router. The extension receives `sender = router` and evaluates `allowedSwapper[pool][router]`. There is no path through which the router forwards the originating EOA's address as `sender`.

The same mismatch exists in `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only) with `SwapAllowlistExtension` must allowlist the router address if they want legitimate users to reach the pool through the standard periphery. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every caller of the router — including users who were never individually approved. The per-user allowlist is completely ineffective for router-mediated swaps. Any unprivileged user can trade in the curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), bypassing the access control the pool admin believed was enforced.

This is an admin-boundary break: the pool admin's configured protection is silently voided by a public, permissionless periphery path.

---

### Likelihood Explanation

The scenario is straightforward and requires no special privileges:

1. The pool admin deploys a pool with `SwapAllowlistExtension` and allowlists individual users.
2. To let those users trade through the standard router, the admin also allowlists the router address — a natural and expected operational step.
3. Any user observing the allowlist state (public mappings) sees that the router is approved and calls `exactInputSingle` directly.

No frontrunning, flash loans, or oracle manipulation are required. The only precondition is that the admin has allowlisted the router, which is the expected configuration for any pool that intends to support the standard periphery.

---

### Recommendation

The `sender` argument forwarded to `beforeSwap` must represent the originating user, not the intermediate router. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should pass the originating `msg.sender` as an explicit `sender` field in `extensionData` (or as a dedicated swap parameter), and the pool should forward it to the hook instead of its own `msg.sender`.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the true originator from `extensionData` when the direct caller is a known router, or the pool interface should be extended to carry a separate `originator` field through the hook call chain.

Until the hook receives the true originating address, the `SwapAllowlistExtension` cannot enforce per-user access control for router-mediated swaps.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Admin: setAllowedToSwap(pool, alice, true)          // alice is KYC'd
3. Admin: setAllowedToSwap(pool, router, true)          // needed so alice can use the router
4. Bob (not KYC'd) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           recipient: bob,
           ...
       })
5. Router calls pool.swap(bob, ...) — pool's msg.sender = router.
6. Extension checks allowedSwapper[pool][router] → true.
7. Bob's swap executes. Allowlist bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
