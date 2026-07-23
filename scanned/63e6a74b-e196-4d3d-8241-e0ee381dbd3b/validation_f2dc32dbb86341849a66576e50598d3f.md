### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When any user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the extension checks whether the **router** is allowlisted — not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every non-allowlisted user can bypass the gate by calling through the router.

---

### Finding Description

**Pool → Extension call chain:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` encodes and dispatches this to the extension:

```solidity
// ExtensionCalling.sol
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, ...)
)
```

`SwapAllowlistExtension.beforeSwap()` then checks `sender` against its allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**Router call chain:**

Every router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly with no mechanism to forward the original user's identity:

```solidity
// MetricOmmSimpleRouter.sol – exactInputSingle
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← extension data is user-controlled but ignored by SwapAllowlistExtension
);
```

The pool sees `msg.sender = router`. The extension sees `sender = router`. The actual user's address is never visible to the extension.

**The dilemma this creates for pool admins:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Legitimate users cannot swap through the router at all (broken core functionality) |
| **Allowlist the router** | Every non-allowlisted user bypasses the gate by calling through the router |

There is no configuration that simultaneously allows router-mediated swaps and enforces per-user allowlisting.

**Contrast with `DepositAllowlistExtension`:**

The deposit allowlist correctly checks `owner` (the economic beneficiary of the position), not `sender` (the caller). This means a non-allowlisted caller cannot mint LP shares to themselves by routing through the liquidity adder. The swap allowlist has no equivalent — it checks `sender`, which collapses to the router address for all router-mediated swaps.

---

### Impact Explanation

Any user can bypass a `SwapAllowlistExtension`-protected pool by calling `MetricOmmSimpleRouter.exactInputSingle()` (or any other router function) instead of calling `pool.swap()` directly. The allowlist — intended to restrict trading to specific addresses (e.g., KYC-verified, institutional, or whitelisted counterparties) — is rendered completely ineffective for router-mediated swaps. Non-allowlisted users can execute swaps, drain pool liquidity, and extract value from pools that were designed to be access-controlled.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that:
1. Configures `SwapAllowlistExtension` to restrict swappers, **and**
2. Allowlists the router (or any other intermediary contract) to support normal user flows

is fully vulnerable. This is the expected production configuration for any access-controlled pool that also wants to support the standard router UX. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — any EOA can execute it.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **economic actor**, not the direct caller. Two approaches:

1. **Pass original caller via `extensionData`:** The router encodes `msg.sender` into `extensionData` for the allowlist extension, and `SwapAllowlistExtension.beforeSwap()` decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`:** For swaps, the `recipient` is the economic beneficiary of the output. Gating by `recipient` is consistent with how `DepositAllowlistExtension` gates by `owner`. This prevents non-allowlisted addresses from receiving swap output regardless of who initiates the call.

Option 2 is simpler and consistent with the existing deposit allowlist design.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for legitimate users.
3. Non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. Attacker successfully swaps in a pool they were never supposed to access, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
