### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` the extension sees — is the router contract, not the end user. If the pool admin allowlists the router so that any legitimate user can reach the pool through the router, every non-allowlisted user can also bypass the gate by routing through the same public contract.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The router stores the real end-user only in transient storage for callback payment purposes — it is never forwarded to the pool as the swap initiator: [5](#0-4) 

This creates the same split-validation pattern as the external report: the guard (allowlist check) operates on one identity (the router), while the economic action (the swap) is performed on behalf of a different identity (the end user). The discrepancy is structural and permanent for any pool that needs to support router-mediated swaps.

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swapping to a curated set of addresses (e.g., KYC-verified wallets, institutional counterparties, or whitelisted protocols). To allow those users to reach the pool through the public `MetricOmmSimpleRouter`, the admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **every address on the network** can bypass the gate by calling any of the router's `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` entry points. The allowlist is rendered completely ineffective for router-mediated swaps, which is the primary user-facing path. Pools relying on the allowlist for regulatory compliance or LP-protection lose that guarantee entirely.

### Likelihood Explanation

The router is a public, permissionless contract. Any user who discovers that the router is allowlisted on a restricted pool can immediately exploit the bypass with no special privileges, no flash loan, and no complex setup. The pool admin has no way to selectively allow legitimate users to use the router without simultaneously opening the gate to all users, so the bypass is reachable whenever the pool is intended to be usable through the router at all.

### Recommendation

The extension must gate on the **end user**, not the immediate caller of `pool.swap()`. Two complementary approaches:

1. **Pass the real initiator through the pool.** Add an `initiator` field to the swap call or to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension then checks `initiator` instead of `sender`. This requires a pool-level or router-level convention.

2. **Check `sender` and `recipient` together.** For router-mediated swaps the recipient is the end user. The extension could require that either `sender` or `recipient` is allowlisted, closing the gap for the common case where the user is also the recipient.

3. **Allowlist the router with a separate per-user check inside the router.** The router could enforce its own allowlist before calling the pool, and the extension could trust the router as a gating intermediary. This requires the router to be a trusted, non-upgradeable contract and the extension to be aware of it.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only intended swapper
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution path:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)         // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives tokens

Result:
  bob swaps successfully despite never being allowlisted.
  The allowlist invariant is broken for all router-mediated swaps.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
