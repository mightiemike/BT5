Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end-user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. If the pool admin allowlists the router — a natural action to let KYC'd users access multi-hop routing — every non-allowlisted user can bypass the restriction by routing through it.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension via `abi.encodeCall`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that forwarded `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original caller:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

At this point `msg.sender` of `pool.swap()` is the **router**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. There is no mechanism in the router or pool's `swap` signature to forward the original caller's identity. The wrong value checked is `allowedSwapper[pool][router]` (always `true` once the router is allowlisted) instead of `allowedSwapper[pool][user]` (which would be `false` for non-KYC'd users).

## Impact Explanation
The `SwapAllowlistExtension` invariant — *only allowlisted addresses may swap* — is silently broken for all router-mediated swaps whenever the router itself is allowlisted. Non-allowlisted users gain unrestricted swap access to a pool explicitly configured to restrict them. For pools used for compliance, restricted LP, or RWA purposes, this constitutes an admin-boundary break: an unprivileged path (`router.exactInputSingle`) bypasses a factory/extension role check, allowing unauthorized parties to drain token reserves via swaps the pool was designed to block.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router, which is a natural and expected administrative action: allowlisted users need the router for multi-hop paths and `exactOutput` flows. A pool admin who allowlists the router to serve their legitimate users unknowingly opens the gate to all users. The trigger is a single, unprivileged `router.exactInputSingle` call — no special role, no front-running, no flash loan required. The condition is highly likely to occur in any real deployment that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

## Recommendation
The extension must gate the economically relevant actor, not the immediate `pool.swap()` caller. The preferred fix is to redesign `SwapAllowlistExtension.beforeSwap` to decode and verify the original user's identity from `extensionData` (with the router cooperating by embedding `msg.sender` there), or to check `recipient` if the intent is to restrict who receives output tokens. Alternatively, document and enforce at the factory level that pools configured with `SwapAllowlistExtension` cannot use a shared router as an allowlisted swapper.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is KYC'd)
  allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)
  bob is NOT in allowedSwapper

Attack:
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
    → router calls pool.swap(recipient=bob, ...)  [msg.sender = router]
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; bob receives tokens

Result:
  bob, a non-allowlisted user, successfully swaps in a pool
  configured to restrict swaps to allowlisted addresses only.
  The allowlist invariant is broken with zero privilege required.
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
