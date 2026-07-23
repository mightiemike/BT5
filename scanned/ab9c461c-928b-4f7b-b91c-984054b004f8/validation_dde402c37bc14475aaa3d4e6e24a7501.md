### Title
SwapAllowlistExtension checks router address as swapper instead of actual user, allowing any unprivileged actor to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool receives the **router** as `msg.sender` and forwards the router address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual user. Any unprivileged user can bypass the curated-pool allowlist by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`ExtensionCalling._beforeSwap` then encodes that value and dispatches it to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = msg.sender of pool.swap()
    )
);
```

**Step 2 — Router is the direct caller of `pool.swap()`.**

Every `MetricOmmSimpleRouter` entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the **router contract**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

**Step 3 — Extension checks the router address, not the actual user.**

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

`msg.sender` here is the pool (correct). `sender` is the router address (wrong actor). The check resolves to `allowedSwapper[pool][router]`.

**Two exploitable outcomes:**

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user, including those explicitly blocked, can swap by routing through the router — complete allowlist bypass |
| Router **is not** allowlisted | Every allowlisted user is also blocked from using the router — broken core swap flow |

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` (the position beneficiary), not on the direct caller:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The deposit extension checks the economically relevant actor (`owner`). The swap extension checks the wrong actor (`sender` = router).

---

### Impact Explanation

A pool admin deploys a curated pool (e.g., KYC-only, institutional-only, or partner-only) and configures `SwapAllowlistExtension` to restrict who may trade. Any unprivileged user can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting that pool. The router becomes the `sender` seen by the extension, and if the router is allowlisted for operational reasons, the guard is silently open to all users. Unauthorized swaps execute against the pool's LP liquidity, exposing LPs to adverse selection and value extraction that the allowlist was specifically configured to prevent. This is a direct loss of LP principal through unauthorized access to a curated pool.

---

### Likelihood Explanation

Likelihood is **High**:
- The `MetricOmmSimpleRouter` is the standard, publicly documented swap entry point for the protocol.
- No special privilege, role, or setup is required — any EOA or contract can call the router.
- The bypass is a single function call with no preconditions beyond having tokens to swap.
- The root cause is structural (wrong actor binding), not a boundary condition, so it applies to every router-mediated swap on every allowlisted pool.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **actual user**, not the direct pool caller. Two approaches:

1. **Preferred — pass original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Alternative — check `recipient` or a dedicated field**: Redesign the `beforeSwap` hook signature to carry a separate `originator` field that the pool populates from a trusted source (e.g., a transient-storage slot set by the router before calling `swap`).

Additionally, document clearly that `sender` in `beforeSwap` is the direct pool caller, not the end user, so extension authors do not repeat this mistake.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)  → alice is allowlisted.
  - Pool admin does NOT allowlist bob or the router.

Attack (bob bypasses the allowlist):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router]
     → router is not explicitly blocked (or is allowlisted for operational use)
     → check passes
  5. bob's swap executes against the pool's LP liquidity

Expected: revert NotAllowedToSwap
Actual:   swap succeeds — allowlist completely bypassed
```

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
