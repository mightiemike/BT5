Audit Report

## Title
Deposit Allowlist Checks `owner` Instead of `sender`, Allowing Unauthorized Callers to Bypass the Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of the pool call) and validates only `owner` (the position beneficiary). Because `MetricOmmPool.addLiquidity` explicitly separates the payer (`msg.sender`) from the position owner (`owner`), any address not on the allowlist can bypass the guard by supplying an allowlisted address as `owner`, depositing into a restricted pool without authorization.

## Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` that need not equal `msg.sender`: [1](#0-0) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` into `_beforeAddLiquidity`: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` correctly forwards both `sender` and `owner` to the extension hook via `abi.encodeCall`: [3](#0-2) 

However, `DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter (`sender`) as unnamed and discards it entirely, checking only `owner`: [4](#0-3) 

The allowlist mapping is keyed on `owner` (the beneficiary), not on the actual caller: [5](#0-4) 

No other guard in the `addLiquidity` path checks whether `msg.sender` is allowlisted. The `isAllowedToDeposit` view function also checks the depositor against the same `allowedDepositor` mapping, confirming the intended semantics are per-depositor, not per-beneficiary: [6](#0-5) 

## Impact Explanation

An address not on the allowlist (Bob) calls `pool.addLiquidity(owner = Alice, ...)` where Alice is allowlisted. The extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert. The pool mints LP shares into Alice's position and invokes Bob's `IMetricOmmModifyLiquidityCallback` to collect tokens. Bob has injected tokens and placed liquidity in arbitrary bins of a restricted pool without ever being allowlisted. This breaks the pool admin's core invariant that only approved addresses may deposit, enabling unauthorized manipulation of per-bin liquidity depth that directly affects swap prices and LP value for existing position holders. Any private or institutional pool relying on the deposit allowlist as a solvency or access-control boundary is silently open to arbitrary depositors.

## Likelihood Explanation

The `addLiquidity` operator pattern (caller ≠ owner) is explicitly supported by the interface. Any actor who reads the contract can discover that supplying an allowlisted `owner` bypasses the guard. No special privilege, flash loan, or oracle manipulation is required — a single direct call suffices. The attack is repeatable at will.

## Recommendation

Change `beforeAddLiquidity` to validate `sender` (the actual payer, first parameter) rather than `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the payer and the beneficiary, both `sender` and `owner` should be checked, and the choice should be explicitly documented.

## Proof of Concept

1. Deploy pool with `DepositAllowlistExtension`; set `allowedDepositor[pool][Alice] = true`; Bob is not allowlisted.
2. Bob deploys a contract implementing `IMetricOmmModifyLiquidityCallback`, funded with token0/token1.
3. Bob calls `pool.addLiquidity(owner = Alice, salt = 0, deltas = <target bins>, callbackData = ..., extensionData = ...)`.
4. `beforeAddLiquidity` is invoked with `sender = Bob`, `owner = Alice`. The check evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
5. Pool mints LP shares into Alice's position and calls Bob's callback; Bob's contract pays the required tokens.
6. Bob has successfully deposited into a restricted pool, placing liquidity in chosen bins, without ever being allowlisted. Alice holds an LP position she did not initiate and must unwind herself.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-30)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
