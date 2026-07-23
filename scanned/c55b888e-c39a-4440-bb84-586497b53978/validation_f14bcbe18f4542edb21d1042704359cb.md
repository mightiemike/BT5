### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which equals `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user of the router bypasses the per-user allowlist.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool via `_callExtensionsInOrder`) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`. [2](#0-1) 

The pool populates `sender` with its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user. [3](#0-2) 

The pool's `addLiquidity` already demonstrates the same pattern — it passes `msg.sender` as `sender` to the extension: [4](#0-3) 

This creates an impossible dilemma for the pool admin:

- **Do not allowlist the router** → all router-mediated swaps revert with `NotAllowedToSwap`, making the router unusable for this pool.
- **Allowlist the router** → every user of the router passes the check, because the extension sees `sender = router` and `allowedSwapper[pool][router] == true`. The per-user allowlist is completely bypassed.

There is no configuration that allows specific users through the router while blocking others.

### Impact Explanation

A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swapping to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool's LP liquidity. LP funds are exposed to the full universe of router users rather than the intended restricted set. This is a direct loss-of-access-control over LP principal with no on-chain mitigation once the router is allowlisted.

### Likelihood Explanation

The router is the standard user-facing entry point documented and deployed alongside the pool. Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps must allowlist the router, triggering the bypass automatically. The attacker needs no special privilege — a single public call to `exactInputSingle` suffices.

### Recommendation

The extension must gate on the **end user**, not the intermediary. Two options:

1. **Pass the original user through the router**: Add a `swapper` field to `extensionData` that the router populates with `msg.sender`, and have `SwapAllowlistExtension` decode and check that field. This requires the extension to trust the router's self-reported identity, which requires a separate router allowlist.

2. **Check `sender` at the pool level before the extension call**: The pool could expose a `tx.origin`-based or EIP-712 signed-identity mechanism, but `tx.origin` is generally unsafe.

3. **Recommended**: Redesign `SwapAllowlistExtension` to accept a trusted-router registry. The extension checks: if `sender` is a trusted router, decode the actual swapper from `extensionData`; otherwise check `sender` directly. The router must be required to embed `msg.sender` in `extensionData`.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension (extension1).
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — necessary so that any router swap passes the check.
3. Alice (not individually allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: alice, ...})
4. Router calls pool.swap(alice, ...) — pool's msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Alice's swap executes against LP liquidity despite never being allowlisted.
``` [5](#0-4) [6](#0-5) [2](#0-1)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```
