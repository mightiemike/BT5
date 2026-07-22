### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the actual end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every unprivileged user can bypass the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the entity calling the extension), and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call. [1](#0-0) 

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the end user:

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

Therefore the allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Direct swap by allowlisted user | Router swap by allowlisted user | Router swap by non-allowlisted user |
|---|---|---|---|
| Allowlist user addresses only | ✓ allowed | ✗ blocked | ✗ blocked |
| Allowlist router address | ✗ blocked (unless also listed) | ✓ allowed | **✓ allowed — bypass** |

There is no configuration that simultaneously (a) allows specific users to swap via the router and (b) blocks non-allowlisted users from doing the same. Allowlisting the router is an all-or-nothing gate that admits every caller.

The same issue applies to multi-hop `exactInput` (intermediate hops use `address(this)` = router as payer/caller) and `exactOutput` recursive callbacks, where the pool's `msg.sender` is again the router. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a swap-allowlisted pool and allowlists the router (the natural step to support standard user flows) inadvertently opens the pool to **any** caller. Non-allowlisted users can execute unrestricted swaps against a pool that was designed to be private or restricted to specific counterparties. This breaks the core access-control invariant of the extension and can lead to unauthorized extraction of LP value from a pool whose liquidity providers expected restricted access.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address. This is the expected operational path, making the trigger condition highly likely in production deployments of allowlisted pools.

---

### Recommendation

The extension must validate the **actual end user**, not the intermediary. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require the router to forward user identity**: Add a dedicated field or a signed attestation in `extensionData` that the extension verifies, falling back to `sender` only when `sender` is not a known router.

At minimum, the `SwapAllowlistExtension` documentation must warn that allowlisting the router grants unrestricted access to all users, and the extension should not be used as a per-user gate when router-mediated swaps are expected.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — the only way to permit router swaps.
3. Non-allowlisted user `attacker` calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `extension.beforeSwap(router, ...)` — `sender` = router address.
6. Check: `allowedSwapper[pool][router]` = `true` → passes.
7. `attacker` successfully swaps on a pool they were never meant to access. [1](#0-0) [2](#0-1) [5](#0-4)

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
