### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any non-allowlisted caller to bypass the deposit guard — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller/payer) and gates only on `owner` (the position beneficiary). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender != owner`, any non-allowlisted address can bypass the deposit restriction by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The pool's own NatSpec explicitly documents the operator pattern: "`msg.sender` pays but need not equal `owner`."

`DepositAllowlistExtension.beforeAddLiquidity` receives both values but discards `sender` (unnamed first parameter) and checks only `owner`:

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
```

The contract's own NatSpec and storage naming state the intent: "Gates `addLiquidity` by **depositor** address" and the mapping key is named `depositor`. The depositor is the actual caller (`sender`), not the position beneficiary (`owner`). The implementation checks the wrong actor.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller) and discards `recipient`:

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry between the two allowlist extensions confirms the deposit extension checks the wrong parameter.

---

### Impact Explanation

The deposit allowlist is completely defeated. Any non-allowlisted address can call `pool.addLiquidity(allowlisted_address, salt, deltas, ...)` directly (or via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlisted_address, ...)`, which only validates `owner != address(0)`). The extension evaluates `allowedDepositor[pool][allowlisted_address]` = `true` and passes. The non-allowlisted caller pays the tokens; the allowlisted address receives the LP shares.

The pool admin's curation policy — restricting which addresses may deposit — is rendered entirely inoperative. Any actor can interact with the pool as a depositor regardless of allowlist status. Additionally, a non-allowlisted actor can force-deposit into an allowlisted user's position at an arbitrary salt without that user's consent, creating unwanted LP positions.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no complex setup. Any EOA or contract can call `pool.addLiquidity` directly with `owner` set to any allowlisted address. The `MetricOmmPoolLiquidityAdder` also exposes a public `addLiquidityExactShares(pool, owner, ...)` overload that accepts an arbitrary `owner`, making the bypass reachable through the supported periphery path as well. Likelihood is high.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller/payer) instead of `owner`:

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

This mirrors the correct actor binding already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  alice  → allowedDepositor[pool][alice] = true
  bob    → allowedDepositor[pool][bob]   = false (not allowlisted)

Attack:
  bob calls pool.addLiquidity(
      owner = alice,   // allowlisted address
      salt  = 99,
      deltas = ...,
      ...
  )

Extension evaluation:
  sender = bob  (discarded, unnamed parameter)
  owner  = alice
  allowedDepositor[pool][alice] == true  →  guard passes

Result:
  bob pays token0/token1 (via callback)
  alice receives LP shares at salt 99
  bob has successfully deposited into a curated pool despite not being allowlisted
  pool admin's curation policy is bypassed
```

**Root cause**: [1](#0-0) 

**Pool operator pattern enabling the bypass**: [2](#0-1) 

**Correct actor binding reference (SwapAllowlistExtension)**: [3](#0-2) 

**Periphery path enabling the bypass**: [4](#0-3)

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
