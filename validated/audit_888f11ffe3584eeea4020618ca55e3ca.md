### Title
SwapAllowlistExtension Gates on Router Address Instead of End User, Enabling Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` from the pool's perspective — i.e., whoever called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that caller is the **router contract**, not the end user. If the pool admin allowlists the router (which is required for any legitimate user to use it), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender` from the pool's perspective: [4](#0-3) 

The router stores the original user only in transient callback context for payment settlement — it is never forwarded to the pool or the extension as the identity to gate: [5](#0-4) 

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every unprivileged user bypasses the allowlist via the router |

There is no code path that lets the pool admin allowlist specific end users while also permitting those users to route through the public router.

---

### Impact Explanation

Any user who is **not** on the swap allowlist can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) targeting a restricted pool. If the router is allowlisted — the only way to make the router usable for legitimate users — the `beforeSwap` guard passes unconditionally for every caller. The pool admin's configured access boundary is completely ineffective. This is an admin-boundary break: an unprivileged path (`router → pool.swap`) causes the factory-registered extension guard to authorize actors the pool admin never intended to allow.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing entry point for swaps. Any pool that intends to use the allowlist while also supporting router-based swaps must allowlist the router, at which point the bypass is immediately reachable by any EOA. The pool admin has no on-chain mechanism to distinguish "router called by an allowlisted user" from "router called by an attacker."

---

### Recommendation

The extension must gate on the **original end user**, not the intermediary. Two viable approaches:

1. **Pass original sender through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trust assumption that the router is the only allowed intermediary.
2. **Require direct pool interaction for allowlisted pools**: Document and enforce that pools using `SwapAllowlistExtension` must not be used with the public router, and add a check in the router that reverts if the target pool has a swap-allowlist extension configured.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   ← required for legitimate users
  allowedSwapper[pool][alice]  = true   ← intended allowlisted user
  allowedSwapper[pool][bob]    = false  ← bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] → true
    → swap executes successfully

Result:
  bob, who is explicitly excluded from the allowlist, completes a swap
  on the restricted pool. The allowlist guard is fully bypassed.
``` [6](#0-5) [7](#0-6)

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
