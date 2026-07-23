The code confirms all claims. Let me verify the key facts:

1. **`DepositAllowlistExtension.beforeAddLiquidity`** (L32-42): First parameter is unnamed/discarded; only `owner` is checked against `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool. [1](#0-0) 

2. **`MetricOmmPool.addLiquidity`** (L191): Passes `msg.sender` as `sender` and caller-supplied `owner` — no `require(msg.sender == owner)` guard. [2](#0-1) 

3. **`removeLiquidity`** (L206): Has `require(msg.sender == owner)` — so the attacker cannot recover tokens via `removeLiquidity` unless the allowlisted owner cooperates. [3](#0-2) 

4. **`addLiquidityExactShares`** (L65): `_validateOwner` only checks `!= address(0)`, allowing any caller to set any non-zero `owner`. [4](#0-3) 

The bypass is real and confirmed. The attacker pays tokens and the allowlisted owner receives LP shares — the attacker cannot unilaterally recover those tokens, but the core invariant (only approved addresses may deposit) is broken for the actual payer/operator dimension.

---

Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Silently Drops `sender`, Allowing Any Unpermissioned Caller to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and validates only `owner` (the position holder). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner` with no equality guard, any unpermissioned address can bypass the deposit allowlist by nominating an allowlisted address as `owner`. This breaks the core access-control invariant of restricted pools.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and a caller-supplied `owner` into the extension hook at line 191:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

There is no `require(msg.sender == owner)` guard in `addLiquidity` (that guard exists only in `removeLiquidity` at line 206). `DepositAllowlistExtension.beforeAddLiquidity` receives both values but the first positional parameter is unnamed and never read:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` accepts a caller-supplied `owner` with no restriction beyond `owner != address(0)` (`_validateOwner`). An unpermissioned caller `B` sets `owner = A` (an allowlisted address); the extension checks `allowedDepositor[pool][A]` → `true` → passes. The actual depositing operator `B` is never checked.

## Impact Explanation
The deposit allowlist is the primary access-control mechanism for restricted pools (e.g., private institutional pools, KYC-gated pools, or pools with regulatory restrictions). Its bypass means any unpermissioned address can add liquidity to a pool it is not authorized to interact with. The unpermissioned caller pays tokens and the allowlisted `owner` receives LP shares they did not request, breaking the core invariant that only approved addresses may deposit. This constitutes an admin-boundary break where an unprivileged path bypasses a pool access-control check enforced by the pool admin.

## Likelihood Explanation
The `MetricOmmPoolLiquidityAdder` is the standard periphery entry point for liquidity addition and explicitly exposes an `owner` parameter that any caller can set to any non-zero address. No additional privilege or special setup is required beyond knowing one allowlisted address, which is observable on-chain from prior `AllowedToDepositSet` events. The bypass is reachable by any EOA or contract in a single transaction with no preconditions.

## Recommendation
Check `sender` (the actual caller) in addition to — or instead of — `owner`, depending on the intended semantics:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    bool poolAllowsAll = allowAllDepositors[msg.sender];
    if (!poolAllowsAll && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate the position holder (`owner`), the current check is correct but the `sender` bypass must be documented explicitly. If the intent is to gate the depositing operator, `sender` must be checked.

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
// Attacker has deposited into a pool it is not authorized to touch.
```

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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
