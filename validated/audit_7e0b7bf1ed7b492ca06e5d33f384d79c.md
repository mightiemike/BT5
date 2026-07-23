The vulnerability is real. Here is the complete analysis:

---

### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Depositors to Bypass the Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is supposed to gate token deposits by depositor address. However, it checks the `owner` parameter (the LP position recipient) rather than the `sender` parameter (the actual caller who pays tokens). Because `MetricOmmPool.addLiquidity` accepts any arbitrary `owner` address with no restriction that `owner == msg.sender`, any non-allowlisted address can bypass the gate by supplying an allowlisted address as `owner`.

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` ignores its first argument (`sender`) entirely and only checks `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool's `addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`, with no enforcement that they match:

```solidity
// metric-core/contracts/MetricOmmPool.sol L182-196
function addLiquidity(address owner, ...) external nonReentrant(...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    ...
}
``` [2](#0-1) 

The periphery's `_validateOwner` only rejects `address(0)` — it does not enforce `owner == msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L247-249
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
``` [3](#0-2) 

### Impact Explanation

A non-allowlisted address (Bob) calls `pool.addLiquidity(owner=alice, ...)` directly, where `alice` is allowlisted. The extension checks `allowedDepositor[pool][alice]` = `true` and passes. Bob's tokens enter the pool via the callback, and Alice receives an LP position she never requested. The pool admin's depositor restriction is completely nullified — any non-allowlisted address can inject tokens into the restricted pool by borrowing any allowlisted address as `owner`. This is a broken admin-boundary: an unprivileged path bypasses the depositor gate.

### Likelihood Explanation

The pool's `addLiquidity` is a public function with no `owner == msg.sender` guard. The attack requires only knowing one allowlisted address (which is public on-chain via `AllowedToDepositSet` events or `allowedDepositor` reads) and implementing the `metricOmmModifyLiquidityCallback`. This is trivially achievable by any EOA or contract.

### Recommendation

Change the check in `beforeAddLiquidity` to validate `sender` (the actual payer/caller) instead of `owner` (the position recipient):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_nonAllowlistedBypassViaOwner() public {
    // alice is allowlisted, bob is not
    depositExtension.setAllowedToDeposit(address(pool), alice, true);
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), bob));

    // bob calls pool.addLiquidity directly with owner=alice
    vm.startPrank(bob);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);
    // bob implements metricOmmModifyLiquidityCallback to pay tokens
    pool.addLiquidity(alice, salt, deltas, callbackData, "");
    vm.stopPrank();

    // assert: bob's tokens entered the pool, alice has LP position
    // allowlist gate was bypassed — bob (non-allowlisted) deposited
}
```

The `sender` argument passed to the extension is `bob` (the actual payer), but the extension checks `owner` = `alice` (allowlisted), so the gate passes. Bob's tokens enter the pool, violating `INVARIANT_only_allowlisted_depositors_deposit_tokens`. [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
