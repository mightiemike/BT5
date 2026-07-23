The code confirms all technical claims. Let me verify the `_beforeSwap` call chain to be thorough.

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks immediate caller (router) instead of actual swapper, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to gate swaps by the actual economic actor's address, per pool. However, the extension receives the **immediate caller of `pool.swap()`** as `sender`, which is the router's address when `MetricOmmSimpleRouter` is used. If the pool admin allowlists the router — the only way to permit router-based swaps for legitimate users — every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. No configuration exists that simultaneously allows router-based swaps for allowlisted users while blocking non-allowlisted users.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap`:**

The check at `SwapAllowlistExtension.sol:37` is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by the pool. [1](#0-0) 

**Call chain — pool passes its own `msg.sender` as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, so whatever address called `pool.swap()` becomes `sender` in the extension. [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and dispatches it to all configured extensions unchanged. [3](#0-2) 

**Router — calls `pool.swap()` directly with no user attestation:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call. No mechanism encodes the actual user (`msg.sender` of `exactInputSingle`) into `extensionData` or any other field visible to the extension. [4](#0-3) 

**Result — irresolvable admin dilemma:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | **Every user** can bypass the per-user allowlist via the router |

The extension's NatSpec states it "Gates `swap` by swapper address, per pool," but the implementation gates by the **immediate caller's** address. [5](#0-4) 

## Impact Explanation
Any user can bypass the swap allowlist of a curated pool by routing through `MetricOmmSimpleRouter`. Curated pools using `SwapAllowlistExtension` may be configured with favorable oracle-priced execution restricted to vetted counterparties. Unauthorized swaps by non-allowlisted users can extract value from LPs through favorable pricing that was intended only for approved parties. This is broken core pool functionality: the configured access guard fails open for all router-mediated swaps once the router is allowlisted, rendering the extension entirely ineffective for its stated purpose.

## Likelihood Explanation
Three conditions are required: (1) the pool uses `SwapAllowlistExtension` — a supported periphery extension; (2) the pool admin allowlists the router — the natural and expected operational action for any pool that wants to support the official periphery router for its allowlisted users; (3) a non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` or any other router entry point. Once conditions (1) and (2) are met, condition (3) requires no special privileges and is trivially reachable by any user. Likelihood: **Medium**.

## Recommendation
The extension must identify the actual economic actor, not the immediate caller. Two viable approaches:

1. **`extensionData` attestation**: Have `MetricOmmSimpleRouter` ABI-encode the actual `msg.sender` into `extensionData` (alongside a router-identity marker), and have `SwapAllowlistExtension.beforeSwap` decode and verify it when `sender` is a known trusted router address.
2. **Dedicated `actualSwapper` field**: Extend the `beforeSwap` hook signature or use a dedicated field so the router can attest the real user's address in a tamper-proof way.

Until fixed, pool admins using `SwapAllowlistExtension` must be warned that allowlisting the router grants unrestricted swap access to all router users.

## Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router to enable router swaps for userA
4. UserB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender of pool.swap() = router address.
6. Pool calls _beforeSwap(sender=router, ...) → ExtensionCalling dispatches to SwapAllowlistExtension.
7. Extension evaluates: allowedSwapper[pool][router] == true → check passes.
8. UserB's swap executes successfully despite not being on the allowlist.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist `userA` and the router, call `exactInputSingle` from `userB` (not allowlisted), assert the swap succeeds and no `NotAllowedToSwap` revert is thrown. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
