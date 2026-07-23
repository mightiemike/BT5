### Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Any Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end user. If the pool admin allowlists the router (the only way to permit router-based swaps for legitimate users), every unprivileged caller can bypass the allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as the first argument. This value is set by `MetricOmmPool.swap` to its own `msg.sender` before dispatching to the extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← pool's msg.sender, not the end user
    recipient,
    ...
    extensionData
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender == pool's msg.sender
)
``` [2](#0-1) 

The extension then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called the pool. When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, the router calls `pool.swap(...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The router carries no per-user identity forwarding mechanism.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (broken functionality) |
| **Allowlist the router** | Every unprivileged user bypasses the allowlist via the router |

### Impact Explanation

Any user can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` against a pool whose `SwapAllowlistExtension` has the router address in `allowedSwapper`. The allowlist — the sole mechanism for restricting who may trade against the pool — is rendered ineffective. Unauthorized users can execute swaps at oracle-anchored prices, draining pool liquidity that was reserved for specific counterparties (e.g., KYC-gated, institutional, or protocol-internal pools). This is a direct breach of the admin-configured access boundary with fund-impacting consequences.

### Likelihood Explanation

The pool admin is forced to allowlist the router to give legitimate users router access. The router is the standard user-facing entry point documented in `metric-periphery`. Any pool that (a) uses `SwapAllowlistExtension` and (b) permits router-based swaps for at least one user is fully exposed. No special privileges, flash loans, or oracle manipulation are required — a single `exactInputSingle` call suffices.

### Recommendation

The router must forward the original caller's identity to the pool so extensions can gate on the true end user. Two complementary fixes:

1. **Router-side**: Store `msg.sender` in transient storage alongside the existing callback context and expose it via a `getSwapOriginator()` view. Pass it as part of `callbackData` or a dedicated field so the pool can relay it.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should accept an optional originator address encoded in `extensionData` and fall back to `sender` only when none is provided. Alternatively, define a separate `ISwapOriginatorProvider` interface that the router implements, and have the extension call it when `sender` is a known router.

Until fixed, pool admins should avoid allowlisting the router and instead require users to call the pool directly.

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists the router so that alice (a legitimate user) can swap via the router.
extension.setAllowedToSwap(pool, address(router), true);

// bob is NOT allowlisted.
// Direct call reverts as expected:
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bob, true, 1000, 0, "", "");

// But bob routes through the router — the extension sees sender == router, which IS allowlisted:
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool:            pool,
    recipient:       bob,
    tokenIn:         token0,
    zeroForOne:      true,
    amountIn:        1000,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp + 1,
    extensionData:   ""
}));
// Swap succeeds — allowlist bypassed.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
