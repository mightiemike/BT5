### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address, not the actual end-user. If the router is allowlisted (necessary for any legitimate user to use it), every user — including non-allowlisted ones — can bypass the restriction by routing through the router.

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` as the `sender` argument to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is what the pool forwarded — the router address when `MetricOmmSimpleRouter` is used.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The pool's `msg.sender` is the router. The extension therefore checks `allowedSwapper[pool][router]`, never `allowedSwapper[pool][actual_user]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → legitimate allowlisted users cannot use the router at all.
- **Allowlist the router** → every user, including non-allowlisted ones, can bypass the restriction by routing through the router.

The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput` paths in the router.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) provides zero enforcement when the router is used. Any unpermissioned user can trade on the restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant), receiving tokens they should not be entitled to receive. This is a direct loss of the pool's access-control invariant and can result in unauthorized fund flows out of the pool.

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any user aware of the router address can exploit this without any special privileges, capital, or setup. The bypass is unconditional whenever the router is allowlisted.

### Recommendation

The `sender` forwarded to extensions must represent the economically relevant actor, not the intermediary. Two viable fixes:

1. **Router passes the real user via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Pool exposes a trusted-forwarder mechanism**: The pool accepts an explicit `originator` parameter (verified against a trusted-forwarder registry) and forwards that as `sender` to extensions instead of `msg.sender`.

The `DepositAllowlistExtension` does not share this flaw because it gates the `owner` parameter (the economic beneficiary of the position), not the `sender`.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice should be able to swap.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` — necessary for Alice to use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully despite not being on the allowlist.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
