Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and gates access on `owner` (the LP-position beneficiary), which is a free caller-controlled argument. Any address not on the allowlist can bypass the guard by naming any allowlisted address as `owner`, completely nullifying the pool admin's access-control intent.

## Finding Description
The `IMetricOmmExtensions.beforeAddLiquidity` interface defines two distinct address parameters: `sender` (first) and `owner` (second). [1](#0-0) 

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner`: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` leaves the first parameter (`sender`) unnamed and unused, then checks only `owner`: [3](#0-2) 

The `allowedDepositor` mapping name and the NatSpec ("Gates `addLiquidity` by depositor address, per pool") confirm the intended subject is the depositing address, not the position beneficiary: [4](#0-3) 

The correct pattern is already implemented in `SwapAllowlistExtension.beforeSwap`, which names and checks `sender` while leaving `recipient` unnamed: [5](#0-4) 

Because `owner` is a free argument supplied by the caller to `addLiquidity`, any address can pass an allowlisted address as `owner` while remaining the actual token-paying `sender`, bypassing the guard unconditionally.

## Impact Explanation
The deposit allowlist is rendered completely ineffective. Any address — regardless of allowlist status — can call `pool.addLiquidity(allowlistedAddress, ...)`, pass the guard, and inject tokens into the pool. The pool admin's access-control intent is silently nullified, breaking the core liquidity-gating invariant the extension is deployed to enforce. This constitutes a broken core pool functionality / admin-boundary break with direct fund-flow impact: unauthorized LPs gain positions in pools intended to be restricted.

## Likelihood Explanation
Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract can trigger it in a single transaction by supplying any allowlisted address as `owner`. The bypass is unconditional whenever the extension is configured on a pool and `allowAllDepositors` is false.

## Recommendation
Replace the unnamed first parameter with `sender` and check it instead of `owner`, mirroring the `SwapAllowlistExtension` pattern:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension` configured; `allowAllDepositors[pool]` is `false`.
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)`; Charlie is not allowlisted.
3. Charlie calls `pool.addLiquidity(Alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(msg.sender=Charlie, owner=Alice, ...)`.
5. Extension receives `(address /*Charlie*/, address owner=Alice, ...)` — `sender` is discarded.
6. Guard evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
7. Charlie's liquidity callback is invoked; Charlie pays the tokens; Alice receives the LP shares.
8. Charlie has deposited into a pool that was supposed to block him, with zero on-chain friction.

### Citations

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-14)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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
