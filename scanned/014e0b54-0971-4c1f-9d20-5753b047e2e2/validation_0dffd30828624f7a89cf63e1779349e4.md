### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's allowlist entry rather than the actual user's. If the pool admin allowlists the router (the natural production step to enable router-mediated swaps), every user on the network can bypass the per-user allowlist by calling any of the router's `exact*` functions.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]   ← wrong identity
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to every extension:

```solidity
// MetricOmmPool.sol L191
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), NOT the end-user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates that argument:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no forwarding of the original caller:

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

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

A pool admin who wants to allow router-mediated swaps must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once that entry exists, the extension passes for every user who calls through the router, regardless of whether that user is individually allowlisted.

---

### Impact Explanation

Any user can bypass the `SwapAllowlistExtension` gate on a restricted pool by routing through `MetricOmmSimpleRouter`. The allowlist's purpose — restricting swaps to a curated set of counterparties (e.g., KYC'd users, institutional traders, whitelisted protocols) — is completely nullified for router-mediated paths. Unauthorized users can execute swaps, drain pool liquidity at oracle-anchored prices, and extract value that the pool operator intended to reserve for approved counterparties only.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary production entry point for end-users. Any pool that deploys `SwapAllowlistExtension` and also expects users to use the router must allowlist the router. This is the standard operational configuration, making the bypass reachable by any user in every such deployment. No special privileges, flash loans, or unusual token behavior are required — a plain `exactInputSingle` call suffices.

---

### Recommendation

Pass the original end-user identity through the extension rather than the direct `pool.swap()` caller. Two options:

1. **Preferred — check `extensionData` for a signed or router-forwarded user address.** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address.

2. **Alternative — check `recipient` instead of `sender` for the swap allowlist.** The recipient is the address that receives output tokens and is harder to spoof without economic loss, though it still allows a non-allowlisted user to route output to an allowlisted address.

Additionally, update the `SwapAllowlistExtension` NatSpec to document that `sender` is the direct caller of `pool.swap()`, not the originating user, so pool admins understand the implication of allowlisting router contracts.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only approved user
  allowedSwapper[pool][router] = true         // admin adds router to enable router swaps

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
      → pool.swap(msg.sender=router, ...)
        → extension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  → PASSES
  bob successfully swaps despite not being individually allowlisted
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
