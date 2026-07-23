Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user, allowing any address to bypass per-user swap gate via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` at swap time. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. A pool admin who allowlists the router — the only way to permit any router-mediated swap — silently opens the gate to every address on-chain, defeating the per-user restriction the extension is designed to enforce.

## Finding Description
In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, recipient, ...)` at line 231, passing its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct key) and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` is called, it invokes `pool.swap(params.recipient, ...)` — making the router the pool's `msg.sender`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The `recipient` (the actual end-user) is available as the second argument to `beforeSwap` but is silently discarded (named `address,` with no identifier). Once the pool admin allowlists the router — the only way to permit any user to swap through the official periphery — every address on-chain can bypass the per-user gate by routing through `MetricOmmSimpleRouter`.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap` with the router as `msg.sender`.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers). Once the router is allowlisted — the expected operational step — the restriction is fully bypassed. Any non-allowlisted address can execute swaps at oracle-quoted prices, causing direct loss of LP principal to pool owners who believed access was gated. This is a broken core pool functionality (allowlist extension) causing potential loss of funds, meeting the Critical/High direct-loss threshold.

## Likelihood Explanation
No special privilege is required. Any user can call `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant) targeting a pool with `SwapAllowlistExtension` active and the router allowlisted. Allowlisting the router is the expected operational path for any pool that wants to support router-mediated swaps, making the bypass reachable in every realistic deployment of this extension. The router is a public, permissionless contract.

## Recommendation
The extension must check the originating user, not the intermediary caller. The `recipient` argument is already passed as the second parameter to `beforeSwap` (currently unnamed/discarded). The simplest fix is to also check `recipient` in the allowlist: `allowedSwapper[pool][sender] || allowedSwapper[pool][recipient]`. A more robust fix is a trusted-forwarder pattern where the router encodes the real `msg.sender` into `extensionData` and the extension decodes and verifies it, but this requires the pool to authenticate the router as a trusted forwarder. At minimum, document that the extension is incompatible with router-mediated flows and require direct pool calls only.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension active.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    // router allowlisted so any user can swap through it
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)
    // alice is NOT allowlisted

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: alice, ...})
  - Router calls pool.swap(alice, zeroForOne, amount, ...)
    // pool's msg.sender = router
  - Pool calls _beforeSwap(sender=router, recipient=alice, ...)
  - Extension checks allowedSwapper[pool][router] → true ✓ (passes)
  - alice's swap executes at oracle price

Expected: revert NotAllowedToSwap (alice is not allowlisted)
Actual:   swap succeeds (router is allowlisted, alice is never checked)
```

Foundry test: deploy pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, router, true)`, then call `router.exactInputSingle` from an address not in the allowlist and assert the swap succeeds rather than reverting with `NotAllowedToSwap`.

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
