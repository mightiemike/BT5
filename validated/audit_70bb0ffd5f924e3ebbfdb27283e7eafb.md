Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position recipient (`owner`) instead of actual depositor (`sender`), allowing full allowlist bypass — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender`/payer) and instead checks `owner` (the position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` accepts a fully attacker-controlled `owner` with no `msg.sender == owner` guard, any unprivileged address can name an already-allowlisted address as `owner` to pass the check, deposit tokens into that address's position, and have the allowlisted accomplice withdraw them via `removeLiquidity`.

## Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` from any external caller and passes `msg.sender` as `sender` to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

There is no `msg.sender == owner` guard anywhere in `addLiquidity`. [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` correctly forwards both actors to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both but **discards `sender`** (unnamed first argument) and checks only `owner`:

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
``` [4](#0-3) 

The allowlist mapping is keyed `allowedDepositor[pool][depositor]` and is intended to gate the depositing actor: [5](#0-4) 

Because `owner` is fully attacker-controlled and the check evaluates `allowedDepositor[pool][owner]`, an unauthorized Bob passes Alice (allowlisted) as `owner`, the guard evaluates `allowedDepositor[pool][Alice]` → `true`, and the deposit proceeds. Bob's tokens are pulled via the liquidity callback and credited to Alice's position. Alice (colluding) then calls `removeLiquidity` — which does enforce `msg.sender == owner` — to recover the funds. [6](#0-5) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper), not the recipient: [7](#0-6) 

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for curating who may provide liquidity. The bypass allows an unauthorized address to add liquidity to a curated pool, breaking the admin-boundary invariant. The unauthorized depositor's funds are credited to the named `owner`'s position; a colluding pair (attacker + allowlisted accomplice) can recover the funds via `removeLiquidity`, effectively laundering unauthorized liquidity. Pool state (bin totals, share accounting) is mutated by an explicitly excluded actor, distorting LP share dilution for legitimate participants. This matches the allowed impact gate: **admin-boundary break — factory/pool role checks bypassed by an unprivileged path**, and **broken core pool functionality** with fund-impacting consequences.

## Likelihood Explanation

No special privilege is required — any EOA or contract can call `pool.addLiquidity` directly. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping or from emitted `AllowedToDepositSet` events. No flash loan or multi-block setup is needed; a single transaction suffices. Likelihood: **High**.

## Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual depositor/payer) instead of `owner` (the position recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

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

1. Pool admin deploys a pool with `DepositAllowlistExtension` on the `beforeAddLiquidity` hook.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is permitted to deposit.
3. Unauthorized Bob calls:
   ```solidity
   pool.addLiquidity(
       alice,         // owner = allowlisted address (attacker-controlled)
       salt,
       deltas,
       callbackData,  // Bob's tokens are pulled here via callback
       extensionData
   );
   ```
4. Pool calls `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. Bob's tokens are deposited; Alice's position grows.
7. Alice calls `removeLiquidity` (passes `msg.sender == owner` check) and withdraws Bob's tokens.
8. The deposit allowlist is fully bypassed in a single unprivileged transaction.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-20)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
