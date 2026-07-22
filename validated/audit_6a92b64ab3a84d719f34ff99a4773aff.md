### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every user on the network can bypass the allowlist by routing through the public router.

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to the extension:

```solidity
// MetricOmmPool.sol
function swap(...) external {
    _beforeSwap(msg.sender, recipient, ...);  // msg.sender = router when routed
}
```

`ExtensionCalling._beforeSwap` encodes and forwards this value unchanged:

```solidity
// ExtensionCalling.sol
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
```

`SwapAllowlistExtension.beforeSwap` then checks whether this `sender` is allowlisted:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router address to permit router-mediated swaps, the check passes for every user who routes through it, regardless of whether that user is individually allowlisted.

### Impact Explanation

**High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is completely bypassed. Any unpermissioned user can execute swaps against the pool's liquidity by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The pool's LP funds are exposed to trades from actors the pool admin explicitly intended to exclude. This constitutes a broken core pool invariant (allowlist policy) with direct fund-impact consequences for LPs.

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the canonical public swap entrypoint. Any user who discovers the allowlist can trivially route through the router. The pool admin enabling router-mediated swaps (by allowlisting the router) is the expected operational pattern, making the bypass condition the default live state.

### Recommendation

The `SwapAllowlistExtension` must gate on the **original end user**, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original initiator through the router**: The router stores `msg.sender` in transient storage (already done for payment in `_setNextCallbackContext`). The pool or extension could read this value. However, this requires a protocol-level convention.

2. **Check `sender` against the original EOA via a forwarded context**: The router should encode the real user in `extensionData` and the extension should decode and verify it — but this is forgeable unless the pool enforces it.

3. **Simplest correct fix**: The pool admin should allowlist individual users only, never the router. Document that allowlisting the router defeats the guard. Alternatively, the extension should revert if `sender` is a known router/contract rather than an EOA, or the pool should expose the original initiator as a separate field.

The cleanest protocol fix is for `MetricOmmSimpleRouter` to encode the real `msg.sender` in `extensionData` and for `SwapAllowlistExtension` to decode and check it, with the pool enforcing that the router-supplied identity cannot be spoofed.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps (or calls `setAllowedToSwap(pool, alice, true)` for a specific user).
3. Unpermissioned user Bob (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` (router is allowlisted).
7. Bob's swap executes against the pool's LP funds despite Bob never being allowlisted.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```
