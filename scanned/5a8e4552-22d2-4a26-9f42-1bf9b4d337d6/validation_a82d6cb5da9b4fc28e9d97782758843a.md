### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (required for any allowed user to reach the pool via the router), every user — including explicitly disallowed ones — can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly: [4](#0-3) 

The pool's `msg.sender` is therefore the router contract. The `sender` value that reaches `SwapAllowlistExtension` is the router address, not the originating user. The extension has no way to distinguish which user initiated the router call.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowed users cannot use the router at all |
| **Allowlist the router** | Every user — including explicitly blocked ones — can bypass the allowlist by routing through the router |

The second branch is the realistic operational path: a pool admin who wants allowed users to be able to use the standard router must allowlist the router, which silently opens the gate to all users.

---

### Impact Explanation

**High.** The `SwapAllowlistExtension` is the sole on-chain mechanism for curating which addresses may swap on a pool. A complete bypass means any disallowed address can execute swaps on a pool that was explicitly configured to exclude them. For pools that use the allowlist to enforce regulatory compliance, institutional-only access, or to prevent adversarial price interaction, this bypass directly undermines the pool's security model and can result in unauthorized token outflows from the pool to disallowed recipients.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery router. Any user who is blocked by the allowlist can trivially route through it. No privileged access, special tokens, or unusual conditions are required. The bypass is reachable on every allowlisted pool whose admin has allowlisted the router.

---

### Recommendation

The extension must gate the **economic actor**, not the technical caller. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require a signed or encoded user identity for router calls**: The extension can detect router-mediated calls (e.g., by checking whether `sender` is a known router) and require the actual user identity to be supplied in `extensionData`.

The simplest safe default is for the extension to treat the router as an untrusted intermediary and require the real user identity to be forwarded explicitly, rather than trusting `sender` to always represent the economic actor.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` as `EXTENSION_1` and `BEFORE_SWAP_ORDER` set to invoke it.
2. Pool admin calls `setAllowedToSwap(pool, allowedUser, true)` — only `allowedUser` should be able to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so `allowedUser` can use the router.
4. `blockedUser` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, ...)`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
8. The swap proceeds. `blockedUser` has successfully bypassed the allowlist. [3](#0-2) [5](#0-4)

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
