### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Position `owner` Instead of Actual `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` argument (the actual caller of `addLiquidity`) and instead checks the LP position `owner`. Because `MetricOmmPool.addLiquidity` allows any caller to deposit on behalf of any owner, an address that is **not** in the allowlist can bypass the guard entirely by depositing on behalf of an allowlisted owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP position owner to `_beforeAddLiquidity`:

```solidity
// MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` then forwards both values to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and silently discarded. The guard checks `owner` instead:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The `isAllowedToDeposit` view function also correctly takes a `depositor` parameter, confirming the intent is to gate the depositor, not the owner:

```solidity
function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
}
``` [5](#0-4) 

`MetricOmmPool.addLiquidity` has no `msg.sender == owner` requirement, so any caller may supply any `owner`: [6](#0-5) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly supports depositing on behalf of a different owner, with `msg.sender` as the payer:

```solidity
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [7](#0-6) 

---

### Impact Explanation

An address not in the allowlist can deposit into a restricted pool by calling `addLiquidityExactShares(pool, allowlisted_owner, ...)`. The extension checks `allowedDepositor[pool][allowlisted_owner]`, which is `true`, so the guard passes. The unauthorized depositor pays the tokens; the position is credited to the allowlisted owner. The pool admin's intent to restrict who can deposit is completely defeated. If the allowlist is used for compliance, KYC, or access-control purposes, this bypass has direct fund-flow consequences: tokens from unauthorized sources enter the pool and LP positions are created outside the intended access boundary.

The inverse also holds: an allowlisted `sender` is blocked from depositing on behalf of a non-allowlisted `owner`, breaking legitimate use of the "deposit on behalf of" pattern for allowlisted operators.

**Severity: Medium** — the allowlist is a pool-admin-configured security boundary; bypassing it is an admin-boundary break with direct liquidity-flow impact.

---

### Likelihood Explanation

The `MetricOmmPoolLiquidityAdder` router explicitly and publicly supports the `owner != msg.sender` deposit pattern. Any actor who knows an allowlisted address (which may be publicly observable on-chain) can exploit this with no special privileges. The only cost is paying the deposited tokens, which the attacker controls.

---

### Recommendation

Name the `sender` parameter and check it instead of `owner`, matching the pattern used by `SwapAllowlistExtension`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured for `beforeAddLiquidity`.
2. Admin calls `setAllowedToDeposit(pool, bob, true)` — only Bob is allowlisted.
3. Alice (not allowlisted) calls `addLiquidityExactShares(pool, bob, salt, deltas, max0, max1, "")` via `MetricOmmPoolLiquidityAdder`.
4. The pool calls `beforeAddLiquidity(router_address, bob, ...)` on the extension.
5. Extension evaluates `allowedDepositor[pool][bob]` → `true` → guard passes.
6. Alice's tokens are pulled; Bob's position is credited. Alice has successfully deposited into a pool she is not authorized to access.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
