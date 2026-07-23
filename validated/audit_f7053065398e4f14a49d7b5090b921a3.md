Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates `owner` instead of `sender`, allowing any unpermissioned caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the actual `msg.sender` of `addLiquidity`, who pays tokens via callback) and instead checks `owner` (a freely caller-supplied parameter). Because any caller can pass an allowlisted address as `owner`, the allowlist gate is trivially bypassed: an unpermissioned address can deposit into a restricted pool by naming any allowlisted address as the position recipient.

## Finding Description
`MetricOmmPool.addLiquidity` dispatches the before-hook with both identities:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly forwards both `sender` and `owner` to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` discards `sender` (unnamed first argument) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

After the hook passes, `LiquidityLib.addLiquidity` calls `metricOmmModifyLiquidityCallback` on `msg.sender` of the pool call (the actual payer), not on `owner`:

```solidity
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
``` [4](#0-3) 

**Exploit path:**
1. Alice is allowlisted: `allowedDepositor[pool][alice] = true`. Bob is not.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")` directly.
3. The extension evaluates `allowedDepositor[pool][alice]` → `true` and does not revert.
4. The pool calls `metricOmmModifyLiquidityCallback` on Bob; Bob's tokens are pulled.
5. Alice receives the LP position. Bob has deposited into a restricted pool without being allowlisted.

The same bypass works through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` where `owner` is caller-supplied and `_validateOwner` only rejects `address(0)`: [5](#0-4) 

In the adder flow, `msg.sender` (Bob) is stored as `payer` in transient context and pays tokens, while `owner` (Alice) receives shares — the same separation applies. [6](#0-5) 

## Impact Explanation
`DepositAllowlistExtension` is the sole on-chain mechanism for pool admins to restrict who may provide liquidity. Checking `owner` instead of `sender` makes the gate completely ineffective: any address can deposit into a restricted pool by naming any allowlisted address as `owner`. This constitutes an admin-boundary break — an unprivileged path bypasses an admin-configured access control — and allows unauthorized liquidity provision into pools designed for controlled LP sets. Additionally, it enables griefing of allowlisted LPs by forcing unwanted positions onto their addresses without their consent.

## Likelihood Explanation
The bypass requires only knowing one allowlisted address (readable from the public `allowedDepositor` mapping) and calling `addLiquidity` directly on the pool or through the adder. No special privileges, flash loans, or oracle manipulation are needed. Every pool that deploys `DepositAllowlistExtension` with a non-empty allowlist is affected on every `addLiquidity` call. [7](#0-6) 

## Recommendation
Replace the unnamed first argument with `sender` and gate on it instead of `owner`:

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

`sender` is `msg.sender` of the `addLiquidity` call — the entity that pays tokens via the callback — which is the economically relevant identity the allowlist is meant to gate.

## Proof of Concept
1. Deploy a pool with `DepositAllowlistExtension` configured as the `beforeAddLiquidity` hook.
2. Call `extension.setAllowedToDeposit(pool, alice, true)` from the pool admin. Bob is **not** allowlisted.
3. From Bob's address, call `pool.addLiquidity(alice, 0, deltas, callbackData, "")` directly.
4. Observe: the extension evaluates `allowedDepositor[pool][alice]` → `true` and does **not** revert.
5. The pool calls `metricOmmModifyLiquidityCallback` on Bob; Bob's tokens are pulled and Alice receives the LP shares.
6. Bob has successfully deposited into a pool he was not permitted to access.
7. Repeat via `adder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")` from Bob to confirm the adder path is equally affected.

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-67)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
