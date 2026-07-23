Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of real swapper, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `MetricOmmPool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. A pool admin who allowlists the router to enable standard periphery UX inadvertently grants every unprivileged user access to the curated pool, completely nullifying the allowlist policy.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` argument:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` inside the extension is the pool (the correct key for the per-pool mapping), but `sender` is whatever the pool passes as the first argument to `_beforeSwap`. The pool always passes its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`:

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
```

The same pattern applies to `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165). In all cases, the allowlist lookup becomes `allowedSwapper[pool][router]` — it checks whether the router is allowed, not whether the real end-user is allowed. There is no mechanism in the extension or the pool to recover the originating EOA from `extensionData` or any other field.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then calls `setAllowedToSwap(pool, router, true)` — the natural operational step to enable standard periphery UX — inadvertently opens the pool to every address on-chain. Any EOA can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool; the extension evaluates `allowedSwapper[pool][router] == true` and the swap proceeds. The allowlist policy — intended to restrict trading to KYC'd, institutional, or otherwise vetted counterparties — is completely nullified. LP capital deposited under the assumption of a restricted counterparty set is exposed to adverse selection by unrestricted traders, constituting a direct loss of LP value. This is an admin-boundary break: an unprivileged path bypasses a pool admin–configured access control.

## Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is the expected operational step for any curated pool that wants to support the standard periphery UX; the admin has no indication from the contract or documentation that doing so opens the pool to all users. The `MetricOmmSimpleRouter` is a public, permissionless contract. Once the router is allowlisted, the bypass is trivially reachable by any EOA with no special privileges, no capital requirements beyond the swap input, and is repeatable indefinitely.

## Recommendation

The extension must check the economically relevant actor — the end-user — not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router should forward `msg.sender` as an encoded field in `extensionData` (or a dedicated parameter), and the extension should decode and check that value instead of `sender`.

2. **Alternatively, document and enforce direct-pool-only swaps on curated pools.** If the pool admin intends to restrict by identity, the extension should reject any `sender` that is a known router unless the real user is also separately allowlisted, or the pool should not allowlist the router at all. This requires the extension to know the router address, which is fragile. The cleanest fix is option 1.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(msg.sender=router, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Swap executes against LP liquidity; `attacker` receives output tokens.

The wrong value is the `sender` argument at `SwapAllowlistExtension.sol` L37: it holds the router address instead of the originating user address, causing `allowedSwapper[pool][router]` to be evaluated in place of `allowedSwapper[pool][attacker]`. [1](#0-0) [2](#0-1) [3](#0-2)

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
