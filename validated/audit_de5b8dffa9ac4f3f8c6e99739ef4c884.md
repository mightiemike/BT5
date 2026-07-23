### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any Caller to Bypass the Per-Pool Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the router is allowlisted (the only way to permit router-mediated swaps on a curated pool), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool sets to `msg.sender` of the `swap()` call:

```solidity
// MetricOmmPool.swap
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` passes it verbatim:

```solidity
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

So `msg.sender` to the pool is the router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The two broken outcomes:**

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user, including explicitly blocked ones, bypasses the per-user allowlist by calling the router |
| Router **is not** allowlisted | Every allowlisted user is also blocked from using the router — the only supported periphery path |

The pool admin has no way to simultaneously permit router-mediated swaps and enforce per-user restrictions. The allowlist invariant is broken in both directions.

The same structural problem exists in the multihop path `exactInput`, which also calls `pool.swap()` with the router as `msg.sender`: [5](#0-4) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., institutional market makers, KYC'd addresses). Any unprivileged user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The router is a public, permissionless contract. No special role or privileged access is required. The disallowed user can drain LP liquidity at oracle-quoted prices, causing direct loss of LP principal on a pool that was explicitly configured to prevent open access.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that configures `SwapAllowlistExtension` and also needs to support router-mediated swaps (the normal production path) must allowlist the router, which immediately opens the bypass to all callers. The trigger requires only a standard `exactInputSingle` call — no flash loans, no callbacks, no admin access.

---

### Recommendation

The extension must identify the economic actor, not the intermediary. Two options:

1. **Pass the original caller through the router**: Have the router encode the original `msg.sender` into `extensionData` and have the extension decode it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Check `sender` only for direct pool calls; decode user from `extensionData` for router calls**: The extension can inspect whether `sender` is a known router and, if so, decode the real user from `extensionData`.

3. **Preferred — gate at the router level**: Add a separate allowlist check in `MetricOmmSimpleRouter` that verifies the caller against the pool's configured allowlist before forwarding the swap, so the pool-level extension always sees the router but the router enforces per-user policy.

The cleanest fix is to have `_beforeSwap` in `ExtensionCalling` pass both `msg.sender` (the direct caller, i.e., the router) and an additional `origin` field (e.g., `tx.origin` or a router-attested address) so extensions can gate on the correct actor.

---

### Proof of Concept

**Setup:**
- Pool deployed with `SwapAllowlistExtension` as `EXTENSION_1`, `beforeSwap` order set.
- Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
- Pool admin does **not** allowlist `attacker`.

**Attack:**
1. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ..., amountIn: X})`.
2. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` with `msg.sender = router`.
3. Pool calls `extension.beforeSwap(router, ...)`.
4. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
5. Swap executes. `attacker` receives tokens from the curated pool despite being explicitly excluded from the allowlist.

**Expected:** revert `NotAllowedToSwap`.
**Actual:** swap succeeds. [1](#0-0) [6](#0-5) [2](#0-1)

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
