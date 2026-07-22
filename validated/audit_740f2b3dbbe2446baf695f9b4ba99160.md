### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract address**, not the end-user. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the per-user allowlist by routing through the same public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool), and `sender` is whatever `msg.sender` the pool received when `swap()` was called. [1](#0-0) 

The pool passes `msg.sender` directly as `sender` to the extension:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

From the pool's perspective, `msg.sender = router address`. The extension therefore checks `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][end_user]`.

This creates an irreconcilable dilemma for pool admins:

| Admin choice | Result |
|---|---|
| Do **not** allowlist the router | Permitted users cannot use the router at all; only direct pool calls work |
| **Allowlist the router** | Every user on the network can bypass the per-user allowlist by routing through the public router |

### Impact Explanation

When a pool admin allowlists the router address to enable router-mediated swaps for their permitted users, the allowlist is completely nullified. Any unpermitted address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the curated pool and the `beforeSwap` hook will pass because it sees the allowlisted router as the swapper. The curated pool's access control is fully bypassed, allowing unauthorized users to trade against LP capital that was deployed under the assumption of a restricted swapper set.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, production-grade swap entry point documented and deployed by the protocol. Pool admins who want their permitted users to have a normal UX (slippage protection, multi-hop, deadline checks) must allowlist the router. The bypass is therefore reachable by any user on any pool that has enabled router-mediated swaps, with no special privileges or setup required.

### Recommendation

Pass the **original end-user** through the swap path so the extension can gate on the economically relevant actor. Two concrete approaches:

1. **Preferred — forward the originating user via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and checks that address instead of `sender`. This requires no core changes.

2. **Alternative — add a `payer` / `originator` field to the swap interface**: The pool accepts an explicit originator address (verified against `msg.sender` or a trusted router registry) and passes it to extensions as a dedicated argument.

Either way, the extension must check the address that controls the economic action, not the intermediate contract that relays it.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is permitted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted for UX
  - bob is NOT in the allowlist

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes successfully for bob despite bob not being allowlisted

Result:
  bob swaps against the curated pool's LP capital, bypassing the intended access control.
  The pool admin's per-user allowlist is completely ineffective for any user who routes
  through the public MetricOmmSimpleRouter.
``` [1](#0-0) [4](#0-3) [2](#0-1) [5](#0-4)

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
