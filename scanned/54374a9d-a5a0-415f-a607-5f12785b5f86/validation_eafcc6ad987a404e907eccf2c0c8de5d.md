### Title
`SwapAllowlistExtension.beforeSwap` gates on the router address, not the originating user — any unprivileged caller bypasses the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the originating user. If the pool admin allowlists the router (required for any legitimate user to swap through it), every unprivileged address can bypass the per-user allowlist by calling the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against `sender`: [1](#0-0) 

`sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool sets to `msg.sender` of the `swap()` call: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`) calls `pool.swap()`, the pool's `msg.sender` is the **router contract**, not the originating EOA: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. This creates an irreconcilable dilemma for any pool admin who wants to enforce a per-user allowlist:

- **Router not allowlisted** → every allowlisted user is silently blocked from using the router; only direct `pool.swap()` calls work.
- **Router allowlisted** (the only way to let legitimate users use the router) → `allowedSwapper[pool][router] == true` passes for **every** caller of the router, including addresses that were never added to the allowlist.

There is no mechanism in the router to forward the originating user's identity to the pool or to the extension.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, private LP pools, compliance-gated venues) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The bypass is unconditional once the router is allowlisted: the non-allowlisted user supplies their own `recipient`, `amountIn`, `zeroForOne`, and `extensionData` through the public router entry points and the pool executes the swap at the oracle-derived price. This constitutes an admin-boundary break where an unprivileged path (`MetricOmmSimpleRouter`) circumvents a configured access-control guard, matching the "factory/oracle role checks are bypassed by an unprivileged path" criterion.

---

### Likelihood Explanation

The scenario is reachable under normal operating conditions. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router must allowlist the router, at which point the bypass is immediately available to every address. The router is a public, permissionless contract. No special privileges, flash loans, or oracle manipulation are required.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **originating user**, not the intermediate caller. Two complementary fixes:

1. **Router-level**: `MetricOmmSimpleRouter` should encode the originating `msg.sender` into `extensionData` (or a dedicated field) so extensions can read the true initiator.
2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` should decode and verify the originating user from `extensionData` when `sender` is a known router, or the pool interface should expose a dedicated `originator` field separate from `sender`.

Until fixed, pool admins should be warned that allowlisting the router nullifies the per-user swap allowlist.

---

### Proof of Concept

```
Pool admin setup:
  allowedSwapper[pool][userA]  = true   // legitimate user
  allowedSwapper[pool][router] = true   // required so userA can use the router

Attack:
  userB (not in allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool:      pool,
        tokenIn:   token0,
        recipient: userB,
        amountIn:  X,
        ...
    })

  Router calls pool.swap() → msg.sender at pool = router
  Pool calls _beforeSwap(sender=router, ...)
  Extension checks allowedSwapper[pool][router] → true → no revert
  userB's swap executes successfully on the restricted pool.
``` [4](#0-3) [5](#0-4) [6](#0-5)

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
