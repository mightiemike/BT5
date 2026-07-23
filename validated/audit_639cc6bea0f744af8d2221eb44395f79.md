Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual depositing caller) and gates on `owner` (the position recipient), which is a caller-controlled parameter. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` address with no allowlist validation, any non-allowlisted address can bypass the deposit gate by nominating any allowlisted address as `owner`. The deposit allowlist — the sole on-chain mechanism for restricting who may deposit into a gated pool — is completely ineffective.

## Finding Description

`ExtensionCalling._beforeAddLiquidity` correctly forwards both actors to every extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L97
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`sender` is `msg.sender` of `MetricOmmPool.addLiquidity` (the router or direct caller); `owner` is the position-recipient parameter supplied by that caller.

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (first parameter is unnamed) and checks `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-38
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The admin-facing setter names the second argument `depositor`, confirming the intent was to gate the actual depositing address, not the position owner:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L18-19
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` with only a zero-address check, then passes `msg.sender` as the payer (token source) and `owner` as the position recipient:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L64-67
_validateOwner(owner);   // only checks owner != address(0)
return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

When the pool calls `addLiquidity(owner, ...)`, `msg.sender` of the pool is the `LiquidityAdder` contract, so `sender` passed to the extension is the `LiquidityAdder` address — not Bob. The extension then checks `allowedDepositor[pool][alice]` (the `owner`), which is `true`, and approves the deposit. Bob's tokens are pulled from Bob via the callback payer context, and Alice receives the LP position.

`SwapAllowlistExtension` correctly gates on `sender` for comparison:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

## Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism for restricting who may deposit into a gated pool (KYC/AML enforcement, private LP pools, deposit cap enforcement). With the check on `owner` instead of `sender`, the guard is completely ineffective: any address can deposit into a restricted pool by nominating any allowlisted address as the position owner. The non-allowlisted depositor's tokens enter the pool and the LP position is credited to the nominated owner. This breaks the core deposit-gating invariant the extension is designed to enforce, constituting a broken core pool functionality causing loss of the access-control guarantee and enabling unauthorized fund flows into restricted pools.

## Likelihood Explanation

The bypass requires no special privilege. Any address can call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with a publicly known allowlisted address (e.g., the pool admin, a known LP, or any address visible on-chain) as `owner`. The allowlisted owner need not cooperate. The attack is trivially repeatable with zero cost beyond gas and token approval.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositing caller), consistent with how `SwapAllowlistExtension` handles `beforeSwap`:

```solidity
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Note that when routing through `MetricOmmPoolLiquidityAdder`, `sender` will be the adder contract address, not the end user. If end-user gating through the adder is required, the adder itself must also be allowlisted and a separate mechanism used to gate the end user, or the pool must be called directly.

## Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — Alice is the only allowlisted depositor.
3. Bob (not allowlisted) approves `MetricOmmPoolLiquidityAdder` to spend his tokens.
4. Bob calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       pool,
       alice,   // owner = allowlisted address
       salt,
       deltas,
       max0, max1,
       extensionData
   );
   ```
5. `LiquidityAdder` calls `pool.addLiquidity(alice, salt, deltas, ...)`.
6. Pool calls `_beforeAddLiquidity(liquidityAdder /*sender*/, alice /*owner*/, ...)`.
7. Extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
8. Bob's tokens are pulled from Bob (payer in callback context) and deposited; Alice receives the LP position.
9. The deposit allowlist has been bypassed by an unprivileged actor with zero cooperation from Alice. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-67)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
