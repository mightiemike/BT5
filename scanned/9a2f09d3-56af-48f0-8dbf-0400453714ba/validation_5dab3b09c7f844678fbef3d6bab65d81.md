### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Curated-Pool Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap`. When `MetricOmmSimpleRouter` is the caller, `sender` equals the router address, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for their permitted users simultaneously grants every user on-chain the ability to bypass the allowlist by routing through the same public router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```
MetricOmmPool.swap (msg.sender = router)
  → _beforeSwap(sender = router, ...)
    → SwapAllowlistExtension.beforeSwap(sender = router, ...)
      → allowedSwapper[pool][router]   ← checked, NOT the originating user
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the pool calls the extension), and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the router is allowlisted, the gate passes for every user who routes through it, regardless of whether that user is individually permitted.

The pool admin faces an inescapable dilemma:
- **Do not allowlist the router** → permitted users cannot use the standard periphery path at all.
- **Allowlist the router** → every unpermitted user can bypass the allowlist by calling `router.exactInputSingle / exactInput / exactOutputSingle / exactOutput`.

### Impact Explanation
Any user can swap on a curated pool that was intended to be restricted to a specific set of counterparties. LPs who deployed capital under the assumption that only vetted users would interact with their pool are exposed to trades from arbitrary actors. This breaks the core access-control invariant of the allowlist extension and constitutes a direct policy bypass with fund-impacting consequences: unauthorized swaps drain LP-owned liquidity at oracle-derived prices the LPs did not consent to offer to the general public.

### Likelihood Explanation
The router is the primary user-facing swap interface for the protocol. Any pool admin who wants their allowlisted users to be able to use the standard periphery (rather than calling the pool directly) must allowlist the router. This is the expected operational pattern, making the bypass reachable in every realistic curated-pool deployment that supports router access.

### Recommendation
The extension must verify the originating user, not the immediate pool caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: the router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. The pool must not allow callers to forge this field (e.g., by requiring the pool to inject it, or by having the router sign it).

2. **Check `recipient` instead of `sender` for swap allowlists**: `recipient` is the address that receives output tokens and is set by the router to `msg.sender` (the actual user). This is already forwarded correctly and cannot be spoofed by the router itself. The extension would check `allowedSwapper[pool][recipient]` instead of `allowedSwapper[pool][sender]`.

Option 2 is simpler and already available in the hook signature (the second argument to `beforeSwap`).

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  allowedSwapper[pool][alice]  = true   // alice is the only permitted user
  allowedSwapper[pool][router] = true   // admin adds router so alice can use periphery

Attack (bob, not on allowlist):
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  router calls pool.swap(recipient=bob, ...) with msg.sender=router
  pool calls extension.beforeSwap(sender=router, recipient=bob, ...)
  extension checks allowedSwapper[pool][router] → true → PASSES
  bob's swap executes on the curated pool
```

Concrete call chain:

- `MetricOmmSimpleRouter.exactInputSingle` [1](#0-0)  calls `pool.swap` with `msg.sender = router`.
- `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap` [2](#0-1) .
- `ExtensionCalling._beforeSwap` encodes `sender` (router) as the first argument to the extension call [3](#0-2) .
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the router, not the originating user [4](#0-3) .

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
