### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument passed by the pool against `allowedSwapper[pool][sender]`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` equals the router address — not the originating user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for their allowlisted users), the allowlist is silently bypassed for every caller, including non-allowlisted ones.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap()` enforces:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the argument the pool forwards from its own `msg.sender` — i.e., whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle()` calls the pool directly:

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

The pool receives `msg.sender = router`. It forwards `router` as `sender` to `_beforeSwap`, which forwards it to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The same identity mismatch applies to `exactOutputSingle`, `exactInput`, and `exactOutput`.

The pool admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — core swap path broken |
| Allowlist the router | Every user, including non-allowlisted ones, can swap through the router — allowlist fully bypassed |

Neither option achieves the intended invariant: "only allowlisted addresses may swap."

---

### Impact Explanation

A pool that deploys `SwapAllowlistExtension` to restrict swap access (e.g., to institutional market makers, KYC'd addresses, or protocol-controlled accounts) is rendered ineffective the moment the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle()` and execute swaps against the restricted pool. This breaks the core access-control invariant of the extension and constitutes an admin-boundary break reachable by an unprivileged path.

Downstream consequences include toxic flow against an oracle-anchored pool that was designed to trade only with trusted counterparties, LP value leakage, and violation of any off-chain compliance or regulatory constraint the allowlist was meant to enforce.

---

### Likelihood Explanation

The trigger is straightforward and requires no special privilege:

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists the router (expected operational step).
2. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool.
3. The extension sees `sender = router`, which is allowlisted, and passes.

The router is a public, permissionless contract. No front-running, flash loan, or privileged access is required.

---

### Recommendation

The pool should forward the **originating caller** rather than its own `msg.sender` as `sender` to extension hooks. One approach is for `MetricOmmSimpleRouter` to pass the end-user address explicitly through the `extensionData` field and for the extension to decode it — but this is fragile and opt-in.

The robust fix is for the pool's `swap()` function to accept an explicit `sender` parameter (as Uniswap v4 does with `msgSender`), or for the router to encode the real user address in a verified field that the pool reads and forwards to extensions. Until then, `SwapAllowlistExtension` cannot safely coexist with the router on a restricted pool.

As a short-term mitigation, `SwapAllowlistExtension` documentation should explicitly warn that allowlisting the router grants unrestricted access to all callers.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the only allowed swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted to enable router swaps

Attack:
  - bob (not allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams{
          pool: restrictedPool,
          ...
      })
  - router calls pool.swap(...) → pool.msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes for bob despite bob not being allowlisted

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
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
