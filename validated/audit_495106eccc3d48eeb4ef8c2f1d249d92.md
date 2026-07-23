Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any caller to bypass the swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][sender]` where `sender` is always `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` is the router contract address, not the end-user. A pool admin who allowlists the router so that permitted users can access the pool through the periphery inadvertently grants unrestricted swap access to every address.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first positional argument, which `MetricOmmPool.swap()` always sets to its own `msg.sender`: [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap(params.recipient, ...)` directly: [2](#0-1) 

So `msg.sender` inside `pool.swap()` is the router contract. The pool then calls `_beforeSwap(msg.sender, ...)` — i.e., `_beforeSwap(router, ...)`: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The `extensionData` bytes forwarded by the router are ignored by `SwapAllowlistExtension` (the parameter is unnamed and unused): [4](#0-3) 

There is no in-band escape hatch. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`.

## Impact Explanation
A pool admin deploying a restricted pool (KYC-gated, institutional counterparties, whitelisted market makers) and allowlisting the router so that permitted users can enjoy slippage protection and deadline enforcement inadvertently opens the pool to all callers. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's LP liquidity without restriction. LP principal is directly exposed to adverse-selection or toxic flow that the allowlist was designed to prevent. This constitutes a broken core pool access-control mechanism causing direct risk to LP funds — matching the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" impact categories.

## Likelihood Explanation
The `MetricOmmSimpleRouter` is the standard user-facing swap entry point. Any pool admin who wants allowlisted users to benefit from periphery features (slippage protection, multi-hop, deadline) must allowlist the router. This is the natural and expected operational step, making the misconfiguration highly probable in any real deployment of a restricted pool.

## Recommendation
The extension must verify the originating user, not the immediate pool caller. Viable approaches:

1. **Router-forwarded identity via `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it using a trusted-forwarder pattern or signature to prevent spoofing.
2. **Document that the router must never be allowlisted**: Require allowlisted users to call `pool.swap()` directly. Safe short-term fix but breaks periphery usability.
3. **Check `tx.origin`**: Only acceptable if the threat model explicitly permits it and the pool is not used from smart contract wallets.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is trusted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // so Alice can use the router

Attack:
  - Charlie (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...)
      → pool passes msg.sender = router as `sender` to _beforeSwap
      → extension checks allowedSwapper[pool][router] → true
  - Charlie's swap executes; allowlist is bypassed.

Result:
  - Charlie trades against LP liquidity intended to be restricted.
  - LP principal is exposed to unrestricted toxic flow.
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
