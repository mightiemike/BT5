### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` at the pool level. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every unpermissioned user can bypass the curated allowlist by routing through the public periphery contract.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap(), not the end user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct pool caller) is allowlisted:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is used, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

At this point `msg.sender` inside `MetricOmmPool.swap()` is the **router address**, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

For any router-mediated swap to work on a curated pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who routes through it, regardless of whether that user is individually permitted.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, institutional traders, or whitelisted market makers) is fully bypassed. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on the restricted pool. This breaks the core curation invariant the extension is designed to enforce and exposes LP funds to adverse selection from actors the pool admin explicitly excluded.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported public swap entrypoint. Any pool that uses `SwapAllowlistExtension` and also wants to support router-based swaps for its permitted users must allowlist the router. Once that is done, the bypass is unconditional and requires no special privileges — any EOA or contract can exploit it by calling the public router functions.

### Recommendation

The `sender` argument forwarded to extensions should represent the **economic actor** (the end user), not the intermediary contract. Two complementary fixes:

1. **In the router**: pass the original `msg.sender` (the end user) as the `recipient` or as a dedicated `swapper` field in `extensionData`, and document the convention.
2. **In `SwapAllowlistExtension`**: gate on the `recipient` argument (the address receiving output tokens) or require the router to embed the real user identity in `extensionData` and decode it in the hook, rather than trusting the `sender` field which is the direct pool caller.

Alternatively, the pool can be documented as incompatible with the public router when `SwapAllowlistExtension` is active, and the factory or extension `initialize` should enforce this constraint.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin allowlists only `trustedUser` for pool.
  - Pool admin also allowlists `router` (MetricOmmSimpleRouter) so that
    trustedUser can swap via the router.

Attack:
  - `attacker` (not in allowlist) calls:
      router.exactInputSingle(ExactInputSingleParams({
          pool: restrictedPool,
          recipient: attacker,
          ...
      }))
  - Router calls pool.swap(attacker, ...) → msg.sender at pool = router.
  - Pool calls _beforeSwap(sender=router, ...).
  - Extension checks allowedSwapper[pool][router] → true (router is allowlisted).
  - Swap executes. Attacker receives output tokens.

Result: attacker bypasses the curated allowlist and trades on a restricted pool.
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
