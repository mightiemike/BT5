### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to make router-mediated swaps work on a restricted pool), every caller of the router — including non-allowlisted addresses — passes the allowlist check unconditionally.

---

### Finding Description

**How the allowlist check works**

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension caller). `sender` is whatever `MetricOmmPool.swap()` received as its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the pool's msg.sender, i.e. whoever called pool.swap()
    recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

**How the router calls the pool**

Every public entry point in `MetricOmmSimpleRouter` calls `pool.swap()` directly from the router contract:

```solidity
// MetricOmmSimpleRouter.sol L72-80  (exactInputSingle)
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

The pool therefore sees `msg.sender = router`. The `sender` forwarded to `beforeSwap` is the router address, not the original EOA.

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (including the recursive callback hops in `_exactOutputIterateCallback`).

**The bypass**

For a pool with `SwapAllowlistExtension` to be usable through the router at all, the pool admin must call:

```solidity
setAllowedToSwap(pool, address(router), true);
```

Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`. Every call to `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router passes the check regardless of who the original `msg.sender` is, because the extension only sees the router address. A non-allowlisted attacker simply calls any router entry point and the guard is silently satisfied.

There is no mechanism in the router to forward the original caller's identity to the pool or to the extension.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's primary mechanism for restricting swap access to specific addresses (e.g., KYC-gated pools, institutional-only pools, or pools with restricted counterparties). When the router is allowlisted — the only configuration that makes router-mediated swaps functional — the allowlist becomes completely ineffective for all router callers. Any address can execute swaps on a nominally restricted pool, draining liquidity or executing trades the pool admin explicitly intended to block. This is a direct loss of the access-control invariant with fund-impacting consequences.

---

### Likelihood Explanation

The scenario is highly likely in practice:

1. Pool admins configure `SwapAllowlistExtension` to restrict swaps.
2. They also want allowlisted users to benefit from the router (slippage protection, multi-hop routing, deadline enforcement).
3. The natural action is to allowlist the router alongside individual users.
4. At that point the allowlist is silently bypassed for every router caller.

No privileged attacker capability is required; any EOA can call the public router functions.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two complementary approaches:

1. **Pass the original caller through the router**: Add an `originSender` field to the extension data that the router populates with `msg.sender` before calling the pool. The extension reads and verifies this field. This requires the extension to trust the router, so the router address itself must be allowlisted as a trusted forwarder, and the extension must validate `msg.sender == trustedRouter` before accepting the embedded identity.

2. **Check `sender` only when it is not a trusted router**: The extension can maintain a set of trusted routers. When `sender` is a trusted router, the extension reads the actual user identity from `extensionData`; otherwise it checks `sender` directly.

Either way, the extension must never grant access based solely on the router's address without also verifying the end user's identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - allowedSwapper[pool][alice] = true   (alice is the intended allowlisted user)
  - allowedSwapper[pool][router] = true  (router allowlisted so alice can use it)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...); pool's msg.sender = router
  - beforeSwap receives sender = router
  - allowedSwapper[pool][router] == true → check passes
  - Bob's swap executes on the restricted pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
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
