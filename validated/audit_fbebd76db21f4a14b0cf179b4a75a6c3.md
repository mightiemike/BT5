Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Trader, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` at swap time. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router (required for any legitimate user to use the periphery), the allowlist is silently bypassed for every user — including those the admin explicitly excluded.

## Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and gates access on it: [1](#0-0) 

`MetricOmmPool.swap` populates that `sender` argument with its own `msg.sender` via `_beforeSwap(msg.sender, recipient, ...)`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(params.recipient, ...)` directly, making the router itself `msg.sender` from the pool's perspective: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. This creates an irreconcilable dilemma: if the admin does not allowlist the router, no legitimate user can use the periphery; if the admin does allowlist the router, every user — including explicitly blocked ones — can bypass the allowlist by routing through `MetricOmmSimpleRouter`. No existing guard in the extension or pool checks the original caller's identity.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin has explicitly chosen which addresses may trade. Once the router is allowlisted (the only way to support the standard periphery), any address can call `MetricOmmSimpleRouter.exactInput` / `exactOutput`, have the router call `pool.swap`, and the extension will pass because it sees the allowlisted router address. The non-allowlisted user receives output tokens directly. This is a direct, fund-impacting bypass of the pool's access-control boundary: unauthorized traders can drain liquidity at oracle-quoted prices from a pool that was supposed to be restricted. Severity: High.

## Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router — a routine operational step for any pool that intends to support the standard periphery. No privileged access, no malicious setup, and no special token behavior is needed. Any public user can execute the bypass in a single transaction.

## Recommendation

Gate `recipient` (the address that receives output tokens) rather than `sender` (the intermediary router), since `recipient` is already passed as the second argument to `beforeSwap`:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, the router can encode the original `msg.sender` into `extensionData` and the extension can decode and check it.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` attached.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the periphery.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` with `recipient = bob`.
5. The router calls `pool.swap(bob, ...)` — pool's `msg.sender` = router.
6. `_beforeSwap(router, bob, ...)` is dispatched; extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes and he receives output tokens despite never being allowlisted.

The same structural bypass applies to `DepositAllowlistExtension` if `MetricOmmPoolLiquidityAdder` is allowlisted as a depositor, since `beforeAddLiquidity` correctly gates on `owner` (second argument) rather than the caller — but the swap extension does not follow this same pattern. [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-40)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```
