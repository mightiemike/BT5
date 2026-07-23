### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the extension checks the router's allowlist status rather than the actual end-user's. If the router is allowlisted (the only way to let legitimate users use it), any non-allowlisted user can bypass the guard entirely by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

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

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

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

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `pool.swap()`:

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

The pool's `msg.sender` is the router, so `sender = router`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

This creates an inescapable dilemma for the pool admin:

| Router allowlist status | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — legitimate allowlisted users cannot use the router |
| Router **allowlisted** | Any user, allowlisted or not, bypasses the guard by routing through the router |

The analog to the external bug is exact: `finalizeRedeem` correctly decrements the user's balance while `finalizeRedeemFor` skips it — one code path enforces the invariant, the parallel path does not. Here, a direct `pool.swap()` call correctly gates the actual caller, while a router-mediated `pool.swap()` call gates the router instead, silently skipping the intended per-user check.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional partners, or whitelisted strategies) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool's LP positions, exposing LPs to counterparties they explicitly excluded. In pools where the allowlist is the primary defense against toxic flow or regulatory non-compliance, this constitutes a broken core pool invariant with direct LP fund exposure.

---

### Likelihood Explanation

Medium-High. Any pool that deploys `SwapAllowlistExtension` and also wants to support the standard periphery router must allowlist the router, which immediately opens the bypass to all users. The router is a public, permissionless contract. No privileged access, special tokens, or malicious setup is required — any user can call `exactInputSingle` on the router pointing at the restricted pool.

---

### Recommendation

The extension must be able to identify the actual end-user, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to not forge the identity.

2. **Check `sender` AND require `sender == tx.origin` for non-router paths, or maintain a router registry**: The extension could maintain a set of trusted routers and, when `sender` is a trusted router, require the actual user identity to be supplied in `extensionData` and verified.

The simplest safe fix is to have the router encode the originating user in `extensionData` and have the extension decode and check that address when `sender` is a known router.

---

### Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension.
// Pool admin allowlists the router so legitimate users can trade.
allowedSwapper[pool][address(router)] = true;

// Alice is allowlisted directly.
allowedSwapper[pool][alice] = true;

// Bob is NOT allowlisted.
// Bob calls pool.swap() directly → reverts (sender = bob, not allowlisted). ✓
// Bob calls router.exactInputSingle({pool: pool, ...}) → succeeds (sender = router, allowlisted). ✗

// Bob has bypassed the allowlist with zero privilege.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
