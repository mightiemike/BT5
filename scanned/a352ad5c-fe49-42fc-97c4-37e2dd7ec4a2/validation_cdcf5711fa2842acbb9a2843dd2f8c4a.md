### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the **position owner** (`owner`) against the allowlist rather than the **actual caller** (`sender`). Any unprivileged user can bypass the allowlist by specifying an allowlisted address as `owner` while themselves acting as the payer, routing through `MetricOmmPoolLiquidityAdder`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead checks `owner`:

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool's `addLiquidity` passes both `msg.sender` (as `sender`) and the caller-supplied `owner` to the extension:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

Because `addLiquidity` imposes no constraint that `msg.sender == owner`, any caller can supply an arbitrary `owner`. The pool's `addLiquidity` does not enforce `msg.sender == owner`: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper), not `recipient`:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [4](#0-3) 

The `MetricOmmPoolLiquidityAdder` allows any user to specify an arbitrary `owner` and acts as the callback payer, making the bypass fully reachable without any special privileges: [5](#0-4) 

The `_validateOwner` check only rejects `address(0)`, not unauthorized callers: [6](#0-5) 

---

### Impact Explanation

A pool admin deploying `DepositAllowlistExtension` to enforce KYC/AML or curated-LP restrictions cannot prevent unauthorized users from depositing. Any user can deposit into the restricted pool by naming an allowlisted address as `owner`. The pool receives tokens from the unauthorized payer, the allowlisted address receives an unsolicited position, and the pool admin's access-control policy is fully nullified. This is a broken core pool functionality: the allowlist guard fails open for every `addLiquidity` call that routes through the `MetricOmmPoolLiquidityAdder`.

---

### Likelihood Explanation

Exploitation requires no special role, no privileged setup, and no non-standard token behavior. Any user who knows one allowlisted address can execute the bypass in a single transaction through the publicly deployed `MetricOmmPoolLiquidityAdder`. Likelihood is **High**.

---

### Recommendation

Change the allowlist check to use `sender` (the actual depositor) instead of `owner` (the position owner), consistent with how `SwapAllowlistExtension` gates swappers:

```solidity
// DepositAllowlistExtension.sol — fix
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `alice`.
2. `bob` (not allowlisted) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "");
   ```
3. `LiquidityAdder` calls `pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), "")` with `msg.sender = LiquidityAdder`.
4. Pool calls `_beforeAddLiquidity(LiquidityAdder, alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. Pool calls `LiquidityAdder.metricOmmModifyLiquidityCallback`; LiquidityAdder pulls tokens from `bob`.
7. Position is credited to `alice`; `bob` has deposited into the restricted pool without being allowlisted. [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
