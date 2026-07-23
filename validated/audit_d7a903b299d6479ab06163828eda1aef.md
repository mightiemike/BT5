### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — the direct caller of `pool.swap()` — against the per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, the pool receives the **router** as `sender`, not the actual end user. Any pool admin who allowlists the router to support router-based swaps for legitimate users inadvertently opens the gate to every user of the public router, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact address:

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

When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

So `sender` seen by the extension is the **router address**, not the original `msg.sender` of `exactInputSingle`. The router is a public, permissionless contract — anyone can call it.

**The dilemma this creates for pool admins:**

| Configuration | Allowlisted users (direct) | Allowlisted users (router) | Non-allowlisted users (router) |
|---|---|---|---|
| Allowlist specific users only | ✅ Blocked correctly | ❌ Blocked (router not listed) | ✅ Blocked |
| Allowlist specific users + router | ✅ Allowed | ✅ Allowed | ❌ **BYPASS** |

There is no configuration that simultaneously (a) allows allowlisted users to use the router and (b) blocks non-allowlisted users from using the router. The extension provides no mechanism to thread the actual caller's identity through the router — it ignores `extensionData` entirely.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, designated market makers, or whitelisted institutions) can be fully bypassed by any user who routes through `MetricOmmSimpleRouter`. Once the router is allowlisted — a necessary step for any allowlisted user who wants to use the standard periphery — the restriction is void. Non-allowlisted users can execute swaps against LP positions that were never intended to be exposed to them, draining LP value through arbitrage or toxic flow that the allowlist was designed to prevent.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who deploys a `SwapAllowlistExtension` and also wants their allowlisted users to access the standard router will naturally allowlist the router address. This is a foreseeable and common configuration. Once that configuration is in place, the bypass requires no special knowledge or privilege — any user simply calls `exactInputSingle` or `exactInput` targeting the restricted pool.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **actual end user**, not the intermediary. Two viable approaches:

1. **Extension-data identity forwarding**: The router encodes `msg.sender` into `extensionData`; the extension verifies a signature or trusted-forwarder proof over that identity. This requires a coordinated change to the router and extension.

2. **Recipient-based gating**: For swap allowlists, gate on `recipient` rather than `sender` when the pool is known to be router-mediated. This is weaker but avoids the router-identity problem.

3. **Router-aware allowlist**: Add a separate `allowedRouter` mapping; when `sender` is a known router, require that `recipient` (or an identity embedded in `extensionData`) is individually allowlisted.

---

### Proof of Concept

```
1. Admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, userA, true)       // allowlist a legitimate user
3. Admin calls setAllowedToSwap(pool, router, true)      // allowlist router so userA can use it
4. Non-allowlisted userB calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: restrictedPool,
           ...
       })
5. Router calls pool.swap(recipient, ...) — msg.sender = router.
6. Pool calls _beforeSwap(router, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
8. userB's swap executes against LP funds that were never meant to be accessible to them.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
