Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the real swapper, allowing any user to bypass the curated-pool allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable standard periphery UX inadvertently grants every unprivileged user access to the curated pool, completely nullifying the allowlist policy.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` argument passed by the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`msg.sender` inside the extension is the pool (correct key for the per-pool mapping), but `sender` is whatever the pool passes as the first argument to `_beforeSwap`. `MetricOmmPool.swap` always passes its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The same pattern applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). In all cases, the router is the direct caller of `pool.swap()`, so the allowlist lookup becomes `allowedSwapper[pool][router]` — checking whether the router is allowed, not whether the real end-user is allowed. [4](#0-3) 

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists `MetricOmmSimpleRouter` (the natural operational step to enable standard periphery UX) inadvertently opens the pool to every user on-chain. Any address can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool; the extension sees `sender = router`, which is allowlisted, and the swap proceeds. The allowlist policy — intended to restrict trading to KYC'd, institutional, or otherwise vetted counterparties — is completely nullified. LP capital deposited under the assumption of a restricted counterparty set is exposed to adverse selection or policy violation by any unprivileged user, constituting a direct loss of LP value. [5](#0-4) 

## Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router address, which is the expected operational step for any curated pool that wants to support the standard periphery UX. The admin has no indication from the contract or documentation that doing so opens the pool to all users. `MetricOmmSimpleRouter` is a public, permissionless contract. Once the router is allowlisted, the bypass is trivially reachable by any EOA with no special privileges, no capital requirements beyond the swap input, and is repeatable indefinitely. [6](#0-5) 

## Recommendation
The extension must check the economically relevant actor — the end-user — not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router should forward `msg.sender` as an extra field in `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and check that value instead of (or in addition to) `sender`.

2. **Alternatively, document and enforce direct-pool-only swaps on curated pools.** If the pool admin intends to restrict by identity, the extension should reject any `sender` that is a known router unless the real user is also separately allowlisted, or the pool should not allowlist the router at all and documentation should make this clear.

The cleanest fix is option 1: have the router propagate the originating user address through `extensionData` and have the extension decode it when present. [5](#0-4) 

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin does **not** allowlist `attacker`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — router is `msg.sender` in the pool.
6. Pool calls `_beforeSwap(msg.sender=router, ...)`, forwarding the router address as `sender` to the extension.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Swap executes against LP liquidity; `attacker` receives output tokens.

The allowlist check at `SwapAllowlistExtension.sol` L37-39 passes because `sender` is the router, not `attacker`. A Foundry integration test can reproduce this by deploying the full stack, configuring the extension, allowlisting only the router, and asserting that an unallowlisted EOA successfully swaps via the router. [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
