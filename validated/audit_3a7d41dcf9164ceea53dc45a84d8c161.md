Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates on Intermediary `sender` Instead of Economic Actor `recipient`, Enabling Full Allowlist Bypass via Any Allowlisted Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — the direct `msg.sender` of `pool.swap()` — against the per-pool allowlist. Because `MetricOmmPool.swap` passes `msg.sender` verbatim as `sender`, any router that is allowlisted (so that permitted users can route swaps) simultaneously grants swap access to every unpermitted user who routes through that same router. The `DepositAllowlistExtension` does not share this flaw: it correctly gates on `owner`, the economic actor, not on the intermediary caller.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` (in `ExtensionCalling`) encodes that value as the first positional argument of the `beforeSwap` call, forwarding it verbatim to every configured extension.

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [2](#0-1) 

The `recipient` parameter — the address that receives output tokens and is the true economic beneficiary — is silently discarded (named `address,` with no binding). When a router calls `pool.swap(recipient=bob, ...)`, the extension sees `sender=router`. If the router is allowlisted, the check passes unconditionally for every user who routes through it.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` (first param) and gates on `owner` (second param), the economic actor whose position is credited: [3](#0-2) 

The structural asymmetry confirms the swap check is bound to the wrong actor.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties loses that restriction entirely the moment the pool admin allowlists any shared periphery router. Every unpermitted address can then execute swaps against the pool by routing through the allowlisted router. Depending on the pool's purpose (e.g., a private market-maker pool, a KYC-gated pool), this allows unauthorized parties to extract value from LP positions at oracle-derived prices, constituting a direct loss of LP assets and a broken core pool invariant. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break by an unprivileged path" impact criteria.

## Likelihood Explanation

The standard periphery entry point for multi-step swaps is `MetricOmmSimpleRouter`. A pool admin who wants permitted users to be able to route will naturally allowlist the router — this is a routine, well-intentioned admin action. The admin has no on-chain signal that doing so opens the gate for all users, because the extension's NatSpec states it "Gates `swap` by swapper address" without clarifying that the swapper is the intermediary, not the end user: [4](#0-3) 

The trigger is therefore a routine, expected admin action, making likelihood high once any router is allowlisted.

## Recommendation

Replace the `sender` check with a check on `recipient` — the address that receives the output tokens and is the true economic beneficiary of the swap:

```solidity
function beforeSwap(
    address,          // sender — ignore
    address recipient,
    bool, int128, uint128, uint256, uint128, uint128,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

This aligns with `DepositAllowlistExtension`'s pattern of gating on the economic actor. Also update the NatSpec and admin documentation to make explicit which address is being gated.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Admin calls `setAllowedToSwap(pool, router, true)` — allowlists `MetricOmmSimpleRouter` so Alice can route multi-hop swaps.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.swap(pool, ...)`.
5. The router calls `pool.swap(recipient=bob, ...)` with `msg.sender=router`.
6. `MetricOmmPool.swap` passes `msg.sender` (router) as `sender` to `_beforeSwap`: [5](#0-4) 
7. `SwapAllowlistExtension.beforeSwap` receives `sender=router`; `allowedSwapper[pool][router]==true` → check passes. [6](#0-5) 
8. Bob's swap executes at oracle prices against LP liquidity — allowlist fully bypassed.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
