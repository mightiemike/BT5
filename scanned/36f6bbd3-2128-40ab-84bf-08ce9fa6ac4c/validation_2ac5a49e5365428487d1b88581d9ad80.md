### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the per-user allowlist via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the original user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user on-chain can bypass the per-user allowlist by routing through it.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is whatever the pool passed as the first argument to the hook. The pool always passes its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
)
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (
    sender, recipient, zeroForOne, ...
))
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

At the point `pool.swap()` executes, `msg.sender` is the **router address**. The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The pool admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Allowlist the router | Every user on-chain can bypass the allowlist via the router |
| Do not allowlist the router | Individually allowlisted users cannot use the router at all |

There is no configuration that simultaneously (a) permits allowlisted users to use the router and (b) blocks non-allowlisted users from using the router.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC-verified addresses, institutional traders, or whitelisted market makers) can be accessed by any arbitrary address by routing through `MetricOmmSimpleRouter`. Unauthorized swappers can trade against the pool's LP liquidity, exposing LPs to adversarial flow (MEV, sandwich attacks, or directional pressure) that the allowlist was specifically deployed to prevent. This constitutes a direct loss path for LP principal.

### Likelihood Explanation

Medium-high. The router is the standard user-facing entry point for the protocol. Any pool admin who wants allowlisted users to be able to use the router (the normal UX) must allowlist the router address, which immediately opens the bypass to all users. The attacker requires no special privilege — only knowledge of the pool address and the router.

### Recommendation

The extension must verify the **original initiating user**, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Extension-data attestation**: Require the router to embed the original `msg.sender` in `extensionData` and have the extension verify it (requires a trusted router or a signed attestation).
2. **Check `sender` only for direct callers; require explicit user identity via `extensionData` for router paths**: The extension can detect router-mediated calls (e.g., `sender` is a known router) and fall back to a user identity embedded in `extensionData`.

The current design of checking `sender` (the immediate pool caller) is structurally incompatible with a router-mediated flow where the router is the pool's `msg.sender`.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][alice]   = true   (intended allowlist)
  pool admin: allowedSwapper[pool][router]  = true   (needed for router UX)

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: ..., ...})

  router calls:
    pool.swap(recipient, zeroForOne, amount, ...)
    // msg.sender to pool = router

  pool calls:
    extension.beforeSwap(sender=router, ...)
    // msg.sender to extension = pool

  extension evaluates:
    allowedSwapper[pool][router] == true  ✓  → swap proceeds

Result:
  charlie successfully swaps against the pool.
  The allowlist check on alice/bob is never reached.
  Any user can repeat this to drain LP value through unauthorized swap flow.
``` [5](#0-4) [6](#0-5)

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
