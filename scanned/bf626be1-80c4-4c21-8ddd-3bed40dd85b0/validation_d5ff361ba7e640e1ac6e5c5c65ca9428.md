### Title
SwapAllowlistExtension Gates on the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the user. If the pool admin allowlists the router to enable router-mediated swaps for curated users, every unprivileged user can bypass the allowlist by calling the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the first argument the pool passes into `_beforeSwap` via `ExtensionCalling`. That argument is `msg.sender` of the `pool.swap()` call — i.e., whoever called the pool directly.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool as:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The router is `msg.sender` to the pool. The pool therefore passes `sender = router` to `ExtensionCalling._beforeSwap`, which encodes it and calls `extension.beforeSwap(sender=router, ...)`. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same substitution occurs in `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops inside `_exactOutputIterateCallback`).

This creates an irreconcilable conflict for the pool admin:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | Blocked (unusable) | Blocked |
| Yes | Passes | **Also passes — bypass** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swap access to a known set of counterparties is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps at oracle-anchored prices against LP capital that was deposited under the assumption that only vetted counterparties would trade. This constitutes a direct loss of LP principal through unauthorized price-taking against the pool's liquidity.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entry point. Pool admins who want allowlisted users to be able to use the router must allowlist the router address. The moment they do, the allowlist is open to all users. The trigger requires no special privilege — any user with a standard ERC-20 approval can call the router.

---

### Recommendation

The pool should forward the original user's identity to the extension rather than its own `msg.sender`. One approach: add an explicit `sender` parameter to `pool.swap()` that the router populates with `msg.sender` (the original user), and the pool forwards that value to `_beforeSwap`. Alternatively, the extension can read the original payer from the router's transient callback context, but that couples the extension to a specific router implementation.

The simplest correct fix is for the pool to accept and forward an explicit `sender` address supplied by the router, so the extension always sees the economically relevant actor regardless of which periphery contract intermediates the call.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)` (the second call is required for alice to use the router at all).
3. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. Router calls `pool.swap(bob, ...)` — pool's `msg.sender` is `router`.
5. Pool calls `extension.beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes against LP capital, bypassing the allowlist entirely.

**Relevant code locations:**

`SwapAllowlistExtension.beforeSwap` — the check that uses `sender` (the router address): [1](#0-0) 

`ExtensionCalling._beforeSwap` — the pool encodes `sender` (its own `msg.sender`) and dispatches to the extension: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` — the router calls `pool.swap()` with itself as `msg.sender`, not the original user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInput` — same substitution for every hop in a multi-hop path: [4](#0-3)

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
