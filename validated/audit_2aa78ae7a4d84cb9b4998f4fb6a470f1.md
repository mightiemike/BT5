### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of the pool. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for allowlisted users, every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`.

This creates an irreconcilable tension for any pool admin who wants to run a curated pool while still supporting the router:

| Admin choice | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Do **not** allowlist the router | ✗ broken | ✗ blocked |
| **Allowlist the router** | ✓ works | **✓ bypass — all users pass** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The root cause is that the extension checks the wrong actor: the immediate caller of the pool (`sender`) rather than the economic actor (the end user).

The analog to the external Yearn bug is exact: just as `_debtOutstanding` and `_creditAvailable` compute the same quantity through two different code paths and produce inconsistent answers, the allowlist check produces two different answers for "is this user allowed to swap?" depending on whether the entry path is a direct pool call or a router-mediated call.

---

### Impact Explanation

Any unprivileged user can trade on a pool that was designed to be restricted (KYC-gated, institutional, regulatory-compliance) by routing through `MetricOmmSimpleRouter`. The allowlist extension provides no protection once the router is allowlisted. This is a direct policy bypass with fund-impacting consequences: unauthorized users can drain or manipulate a pool whose LP composition was curated under the assumption that only vetted counterparties could trade.

---

### Likelihood Explanation

Medium-High. The pool admin must allowlist the router for the bypass to activate, but this is the natural and expected action: any admin who wants allowlisted users to be able to use the supported periphery router will allowlist it. The admin has no reason to suspect that allowlisting the router opens the pool to all users, because the extension's name and documentation present it as a per-user gate. The bypass is then available to any unprivileged user with no further preconditions.

---

### Recommendation

The `beforeSwap` hook must check the economic actor, not the immediate caller. Two concrete options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks it. This requires a coordinated convention between the router and the extension.
2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is often the relevant identity. This is not always equivalent but avoids the router-indirection problem.
3. **Document the limitation explicitly**: If the design intent is that `sender` is the immediate caller, the extension must warn that allowlisting any intermediary (router, multicall, aggregator) opens the pool to all users of that intermediary.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin: setAllowedToSwap(pool, alice, true)
   — Alice is the only intended swapper.
3. Pool admin: setAllowedToSwap(pool, router, true)
   — Admin wants Alice to be able to use MetricOmmSimpleRouter.
4. Eve (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: eve, ...})
5. Router calls pool.swap(...) — msg.sender to pool = router.
6. Pool calls _beforeSwap(router, eve, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Eve's swap executes on the curated pool.
   allowedSwapper[pool][eve] was never checked.
```

The corrupted value is `allowedSwapper[pool][router]` being used as a proxy for per-user authorization when it is actually a blanket pass for every user of the router. [5](#0-4) [6](#0-5) [1](#0-0)

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
