Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` parameter (the actual depositor who pays tokens and is called back) and instead checks `owner` (the LP-position beneficiary, a free caller-supplied argument) against the per-pool allowlist. Because `owner` is arbitrary, any address not on the allowlist can deposit into a restricted pool by naming any allowlisted address as `owner`. The guard enforces the wrong actor, rendering the depositor restriction entirely ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` accepts `owner` as a caller-supplied argument and passes `msg.sender` as `sender` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both `sender` and `owner` and forwards them to the extension in that order: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (unnamed first parameter) and checks only `owner`: [3](#0-2) 

After the hook passes, `LiquidityLib.addLiquidity` calls back to `msg.sender` (the actual depositor) to pull tokens, and credits shares to `owner`: [4](#0-3) 

The asymmetry is confirmed by comparing with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the swapper) and discards the `recipient`: [5](#0-4) 

The `allowedDepositor` mapping is public, so any attacker can read a valid allowlisted address and use it as `owner`. No existing guard prevents this: `BaseMetricExtension.onlyPool` only verifies the caller is a registered pool, not the depositor identity. [6](#0-5) 

## Impact Explanation
Admin-boundary break: the pool admin configures `DepositAllowlistExtension` believing it restricts which addresses can deposit tokens (the NatSpec states "Gates `addLiquidity` by depositor address, per pool"). That restriction does not exist. Any unprivileged address can deposit tokens into a pool intended to be closed (e.g., KYC-gated or institutional-only pools) by supplying an allowlisted address as `owner`. The compliance boundary is entirely absent. Additionally, an allowlisted depositor who legitimately wants to create a position for a non-allowlisted `owner` is incorrectly blocked, breaking the intended liquidity flow in that configuration.

## Likelihood Explanation
Exploitation requires no special privilege. Any EOA or contract can call `pool.addLiquidity` directly. The only prerequisite is knowing one allowlisted address for the target pool, which is readable from the public `allowedDepositor` mapping. The bypass is a single-transaction, zero-setup operation with no cost beyond gas and the deposited tokens (which the attacker controls).

## Recommendation
Mirror the pattern used by `SwapAllowlistExtension`: check `sender` (the depositor) instead of `owner` (the position holder).

```solidity
// DepositAllowlistExtension.sol — corrected beforeAddLiquidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate by both depositor and owner, both should be checked explicitly.

## Proof of Concept

Setup:
- Pool `P` has `DepositAllowlistExtension` configured.
- `allowedDepositor[P][alice] = true`; Bob is not allowlisted (readable from the public mapping).

Attack:
1. Bob calls `P.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
2. Pool calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
3. Extension evaluates `allowedDepositor[P][alice]` → `true` → no revert.
4. `LiquidityLib.addLiquidity` executes; pool calls `IMetricOmmModifyLiquidityCallback(msg.sender).metricOmmModifyLiquidityCallback(...)` — i.e., calls back to Bob to pull tokens.
5. Bob pays tokens; Alice receives LP shares she did not request.
6. Bob has deposited into a pool that was supposed to be closed to him. The allowlist guard was never triggered.

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-157)
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

      emit IMetricOmmPoolActions.LiquidityAdded(owner, salt, deltas.binIdxs, binBalanceDeltas, deltas.shares);
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
