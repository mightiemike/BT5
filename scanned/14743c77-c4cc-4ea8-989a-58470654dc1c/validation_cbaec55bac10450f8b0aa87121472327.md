### Title
`SwapAllowlistExtension#beforeSwap()` Checks the Router's Address Instead of the Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension` is designed to gate `swap` by swapper address per pool. However, the `sender` argument it receives is `msg.sender` from the pool's perspective — which is the router when a user swaps through `MetricOmmSimpleRouter`. If the pool admin allowlists the router to support router-mediated swaps, every user on the public internet can bypass the allowlist by routing through it.

### Finding Description

The pool's `swap()` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:231
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:95-97
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
  (sender, recipient, zeroForOne, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) calls `pool.swap()`, the pool sees `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

So the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. The actual user's identity is never visible to the extension.

This creates two mutually exclusive broken states:

| Router allowlisted? | Effect |
|---|---|
| **Yes** (to support router-mediated swaps) | Every non-allowlisted user bypasses the gate by going through the router |
| **No** | Every allowlisted user is blocked from using the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

### Impact Explanation

A pool admin who deploys a curated pool (e.g., a KYC-gated or institutional pool) and allowlists the router to support normal UX loses all access control. Any unprivileged user calls `router.exactInputSingle()` with the curated pool as target; the extension sees `sender = router`, finds it allowlisted, and permits the swap. LP principal in the curated pool is exposed to unrestricted trading, directly violating the pool's intended invariant. This is a direct loss of curation control with fund-impacting consequences (bad-price execution by unintended counterparties, LP value leakage).

### Likelihood Explanation

The router is the standard user-facing entry point documented and tested throughout the periphery. Any pool admin who wants their allowlisted users to have a normal swap UX must allowlist the router, triggering the bypass. The attacker needs no special privilege — only the ability to call a public router function.

### Recommendation

The extension must gate the economically relevant actor, not the pool's direct caller. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated convention between router and extension.

2. **Use `tx.origin` as a fallback identity**: When `sender` is a known router (or any non-EOA), fall back to `tx.origin`. This is safe in this context because the extension is only checking identity for allowlist purposes, not for payment authorization.

3. **Redesign the pool's `sender` binding**: Have the pool accept an explicit `swapper` parameter distinct from `msg.sender`, which the router populates with `msg.sender` before calling `pool.swap()`. The extension then checks that explicit swapper.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted for UX
4. bob (non-allowlisted EOA) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap()
   → pool passes msg.sender=router as `sender` to _beforeSwap
   → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
   → bob's swap succeeds despite not being allowlisted
5. alice calls pool.swap() directly with sender=alice → correctly allowed.
   alice calls router.exactInputSingle() → sender=router → also allowed (but for wrong reason).
   bob calls pool.swap() directly → sender=bob → correctly blocked.
   bob calls router.exactInputSingle() → sender=router → incorrectly allowed. ✗
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
