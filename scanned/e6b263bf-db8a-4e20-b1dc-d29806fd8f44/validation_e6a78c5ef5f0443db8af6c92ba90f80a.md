### Title
`DepositAllowlistExtension.beforeAddLiquidity()` checks `owner` but not `sender`, allowing any unprivileged caller to bypass the deposit allowlist - (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity()` validates the `owner` parameter (the LP position beneficiary) but silently discards the `sender` parameter (the actual `msg.sender` of the `addLiquidity` call). Because `owner` is a free caller-controlled argument to `MetricOmmPool.addLiquidity()`, any address not on the allowlist can bypass the deposit gate by passing an allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity()` accepts a caller-supplied `owner` address and forwards both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both `sender` and `owner` and passes them to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity()` receives `sender` as its first argument but discards it (unnamed `address`), checking only `owner`:

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

The position key is `keccak256(abi.encode(owner, salt, bin))`, so the position is recorded under the supplied `owner`, not under `msg.sender`: [4](#0-3) 

Tokens are pulled from `msg.sender` via the modify-liquidity callback: [5](#0-4) 

### Impact Explanation

An unprivileged caller (Bob, not on the allowlist) calls:

```
pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)
```

The extension checks `allowedDepositor[pool][alice]` → `true` → passes. Bob's tokens are pulled via callback and credited to Alice's position. The deposit allowlist — the sole access-control mechanism for restricted pools — is completely bypassed. Any external address can deposit into a pool that the admin intended to restrict to a curated set of LPs.

Secondary consequence: Bob's deposited tokens are permanently locked under Alice's position key. `removeLiquidity` enforces `msg.sender == owner`, so only Alice can withdraw them: [6](#0-5) 

Bob suffers an irrecoverable loss of his deposited principal; Alice receives an unsolicited liquidity position she can drain at will.

### Likelihood Explanation

- No special role or prior approval is required; any EOA or contract can call `addLiquidity` with an arbitrary `owner`.
- The only prerequisite is knowing one allowlisted address, which is trivially discoverable on-chain from `AllowedToDepositSet` events or by querying `allowedDepositor`.
- The attack is a single transaction with no setup cost beyond gas.

### Recommendation

Check `sender` (the actual depositor) instead of — or in addition to — `owner` in `beforeAddLiquidity`. The intent of the allowlist is to gate who provides liquidity, which is the `sender`, not the position beneficiary:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

If the intent is to restrict both who calls and who benefits, check both `sender` and `owner`.

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
3. Bob deploys a contract implementing `IMetricOmmModifyLiquidityCallback` that transfers the required tokens in `metricOmmModifyLiquidityCallback`.
4. Bob calls `pool.addLiquidity(alice, 0, deltas, callbackData, "")`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` credits shares to `positionBinShares[keccak256(alice, 0, bin)]` and pulls Bob's tokens via callback.
7. Bob has deposited into a restricted pool without being on the allowlist. Alice now holds the position and can call `removeLiquidity` to claim Bob's tokens. [3](#0-2) [1](#0-0)

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-155)
```text
      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
      }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L256-259)
```text
  function _positionBinKey(address owner, uint80 salt, int8 bin) internal pure returns (bytes32 key) {
    // forge-lint: disable-next-line(asm-keccak256)
    return keccak256(abi.encode(owner, salt, bin));
  }
```
