Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Ignores `sender`, Allowing Unpermissioned Callers to Bypass Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` receives the actual caller (`sender`) as its first argument but silently discards it, checking only `owner` (the position holder) against the allowlist. Because `MetricOmmPool.addLiquidity` permits `msg.sender ≠ owner` with no equality guard, any unpermissioned address can bypass the deposit allowlist by supplying an allowlisted address as `owner`, breaking the core access-control invariant of restricted pools.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and a caller-supplied `owner` into the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both values to the extension:

```solidity
// ExtensionCalling.sol lines 95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter unnamed and never reads it — only `owner` is checked:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` with only a non-zero check, then calls the pool with `msg.sender` as the payer:

```solidity
// MetricOmmPoolLiquidityAdder.sol lines 56-68
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);  // only checks != address(0)
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [4](#0-3) 

**Exploit path:**
1. Pool P has `DepositAllowlistExtension`; only address A is allowlisted (`allowedDepositor[P][A] = true`).
2. Unpermissioned address B calls `LiquidityAdder.addLiquidityExactShares(pool=P, owner=A, ...)`.
3. The adder calls `P.addLiquidity(owner=A, ...)` with `msg.sender = LiquidityAdder`.
4. The pool calls `extension.beforeAddLiquidity(sender=LiquidityAdder, owner=A, ...)`.
5. The extension checks `allowedDepositor[P][A]` → `true` → passes.
6. B successfully deposits tokens into pool P without being allowlisted.

The same path works when B calls `pool.addLiquidity(owner=A, ...)` directly, since `addLiquidity` has no `require(msg.sender == owner)` guard. [5](#0-4) 

## Impact Explanation
The deposit allowlist is the primary access-control mechanism for restricted pools (KYC-gated, institutional, regulatory). Its bypass means any unpermissioned address can add liquidity to a pool it is not authorized to interact with. The unpermissioned caller pays tokens; the allowlisted `owner` receives LP shares they did not request and can subsequently call `removeLiquidity` to extract the underlying tokens. The core invariant — "only approved addresses may deposit" — is broken for the actual payer/operator dimension, constituting a broken core pool functionality with direct fund-flow impact. [6](#0-5) 

## Likelihood Explanation
The `MetricOmmPoolLiquidityAdder` is the standard periphery entry point and publicly exposes an `owner` parameter with no restriction beyond non-zero. Allowlisted addresses are observable on-chain from `AllowedToDepositSet` events. No special privilege or setup is required; the bypass is reachable by any EOA or contract in a single transaction. [7](#0-6) 

## Recommendation
Check `sender` (the actual caller/operator) instead of or in addition to `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate the position holder (`owner`), that must be explicitly documented and the `sender` bypass acknowledged. If the intent is to gate the depositing operator, `sender` must be checked. [3](#0-2) 

## Proof of Concept
```solidity
// Pool P has DepositAllowlistExtension; only `allowedUser` is allowlisted.
// `attacker` is NOT allowlisted.

address allowedUser = ...; // allowedDepositor[P][allowedUser] == true
address attacker    = ...; // allowedDepositor[P][attacker]    == false

vm.prank(attacker);
liquidityAdder.addLiquidityExactShares(
    pool,
    allowedUser,   // allowlisted owner; attacker is the actual payer
    salt,
    deltas,
    maxAmount0,
    maxAmount1,
    ""
);
// Succeeds: extension checks allowedDepositor[P][allowedUser] == true
// attacker has deposited into a pool it is not authorized to touch.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
