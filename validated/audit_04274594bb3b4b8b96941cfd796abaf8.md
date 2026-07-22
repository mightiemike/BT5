### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and forwards that address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual user. If the pool admin allowlists the router to support router-mediated swaps, every unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), but `sender` is the router (wrong). The allowlist lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

This creates an inescapable dilemma for any pool admin who wants to restrict swaps to specific users while also supporting the standard router:

| Admin action | Direct swap by allowlisted user | Router swap by allowlisted user | Router swap by non-allowlisted user |
|---|---|---|---|
| Do **not** allowlist router | ✅ passes | ❌ blocked | ❌ blocked |
| **Allowlist router** | ✅ passes | ✅ passes | **✅ passes — bypass** |

The moment the admin allowlists the router to restore router usability for legitimate users, the allowlist is rendered completely ineffective: any address can call `router.exactInputSingle()` / `exactInput()` / `exactOutputSingle()` / `exactOutput()` and the extension will see `sender = router` (allowlisted) and pass.

The analog to the external `LimitOrderHook` bug is exact: in that report the `removingAllLiquidity` flag is evaluated against state that has already been mutated (liquidity decremented), so the guard always sees the wrong value. Here the allowlist guard is evaluated against an identity that has already been transformed by the call stack (user → router), so the guard always checks the wrong actor when the router is in the path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or a private market-making arrangement). Once the admin allowlists the router — a necessary step for any allowlisted user who wants to use the standard periphery — the restriction is fully lifted for the entire public. Any user can execute swaps against the pool's liquidity, extracting value at oracle-derived prices that the pool's LPs did not consent to offer to the general public. This constitutes a direct loss of LP principal through unauthorized swap execution and breaks the core access-control invariant of the pool.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented swap interface for the protocol. Any pool admin who deploys a swap-allowlisted pool and then tries to let allowlisted users trade normally will discover that direct pool calls work but router calls fail. The natural remediation — allowlisting the router — immediately opens the bypass. The trigger requires no special privileges, no flash loans, and no exotic token behavior: any EOA can call the public router.

---

### Recommendation

The extension must gate on the **original user's identity**, not the intermediate contract's address. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`; the extension decodes and checks that address. This requires a coordinated convention between router and extension.

2. **Check `sender` only for direct pool calls; require `extensionData` attestation for router calls**: The extension distinguishes the two entry paths and applies the appropriate check in each case.

Either way, the invariant that must hold is: `allowedSwapper[pool][X]` is true if and only if `X` is the address the pool admin intended to authorize, regardless of which periphery contract sits between the user and the pool.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is the only authorized swapper)
  allowedSwapper[pool][router] = true  (admin adds this so alice can use the router)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution path:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router)
            → allowedSwapper[pool][router] == true  ✅ passes
        → swap executes, bob receives output tokens

Result: bob swaps successfully despite never being allowlisted.
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
