### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the pool to every user on-chain, completely defeating the allowlist.

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to every before-swap extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so `sender` forwarded to the extension is the router address. The extension never sees the end user's address.

This creates an irresolvable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use the router; swap flows are broken for them |
| Yes | Every user on-chain can bypass the allowlist by routing through the router |

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified market makers, institutional counterparties, or whitelisted bots) is fully bypassed the moment the pool admin allowlists the router to support normal user flows. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput` and execute swaps against the restricted pool. The pool's LP assets are exposed to the full public swap surface the admin intended to gate, breaking the core access-control invariant and constituting broken core pool functionality with direct LP asset exposure.

### Likelihood Explanation

Any deployment that (a) attaches `SwapAllowlistExtension` and (b) expects users to interact via the canonical router will hit this. The router is a standard periphery contract; pool admins will routinely allowlist it. The bypass requires no special privileges, no flash loans, and no unusual token behavior — a plain `exactInputSingle` call suffices.

### Recommendation

Pass the **originating user** through the extension call rather than the immediate pool caller. One approach: add an optional `originSender` field to the extension interface (populated from `tx.origin` or from a trusted router that forwards the real payer address via `callbackData`). Alternatively, `SwapAllowlistExtension` should document that it is incompatible with router-mediated swaps and revert if `msg.sender` (the pool) has the router registered as a caller, or the allowlist check should be performed in the router itself before the pool call, with the pool verifying the router's attestation.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` attached as a before-swap hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes against the restricted pool with no revert, bypassing the allowlist entirely. [3](#0-2) [5](#0-4) [1](#0-0)

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
