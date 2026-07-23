### Title
SwapAllowlistExtension gates on the router's address instead of the actual end-user, allowing any unprivileged swapper to bypass a pool's swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end-user. A pool admin who allowlists the router to support router-mediated swaps for their approved users inadvertently opens the gate to every user on the network, completely defeating the allowlist.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap hook:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry-point) calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

When this executes, `pool.swap`'s `msg.sender` is the router contract. The pool therefore passes `sender = router` to the extension. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. The moment they do, `allowedSwapper[pool][router] = true` and the check passes for **every** caller of the router, regardless of whether they are individually approved.

The same issue applies to all router entry-points: `exactInput`, `exactOutputSingle`, `exactOutput`, and intermediate hops inside `_exactOutputIterateCallback`.

### Impact Explanation

Any user who is not individually allowlisted can bypass the swap restriction by routing through `MetricOmmSimpleRouter`. If the allowlist is used to protect LP funds (e.g., only trusted market makers are permitted to trade against the pool's liquidity), an adversarial swapper can extract value from LPs by routing through the public router. This is a direct loss of LP principal and breaks the core pool access-control invariant.

### Likelihood Explanation

The pool admin must have allowlisted the router for the bypass to work. This is a natural and expected action: any admin who wants their approved users to be able to use the standard periphery router will add the router to the allowlist. The bypass is therefore reachable in any production deployment that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

### Recommendation

The extension should gate on the economically relevant actor. Two options:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives the output tokens and is the economic beneficiary of the swap. This is harder to spoof through a router.
2. **Decode the actual user from `extensionData`** — the router already forwards caller-supplied `extensionData` unchanged; the extension can require the real user's address to be encoded there and verify it against the allowlist, with the router signing or forwarding `msg.sender` in that payload.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension`; `allowAllSwappers[pool] = false`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is approved.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is approved so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully, bypassing the allowlist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
