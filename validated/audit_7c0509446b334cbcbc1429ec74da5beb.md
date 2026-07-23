Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that calls `addLiquidity` and pays tokens via callback) and instead checks `owner` (the LP-position recipient). Because `addLiquidity` accepts an arbitrary `owner` with no restriction, any unprivileged address can bypass the allowlist by naming an allowlisted address as `owner`. This renders the pool admin's deposit access control ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the second argument to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The extension hook receives `sender` as its first argument but leaves it unnamed (discarded), then checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

Since `addLiquidity` imposes no restriction on who can be `owner`, any caller can supply an allowlisted address as `owner`. The extension sees `allowedDepositor[pool][allowlistedAddress] = true` and passes the check, while the actual token payment flows from the unauthorized `msg.sender` via the modify-liquidity callback.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

The asymmetry is the root cause: `SwapAllowlistExtension` gates by `sender`; `DepositAllowlistExtension` gates by `owner`.

## Impact Explanation
The pool admin's deposit allowlist is fully bypassed for the actual depositor. Any address not on the allowlist can call `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)` directly (bypassing the periphery `LiquidityAdder`). The extension sees `owner = allowlistedAddress`, passes the check, and the unauthorized caller pays tokens via the modify-liquidity callback. The allowlisted address receives LP shares; the unauthorized caller has deposited despite not being allowlisted. This is a direct admin-boundary break: a pool admin configures a guard (deposit allowlist) that is bypassed by an unprivileged path.

Secondary impact: a legitimately allowlisted `sender` who specifies a non-allowlisted `owner` is incorrectly blocked, breaking the expected deposit flow. [4](#0-3) 

## Likelihood Explanation
No special privilege is required. Any EOA or contract can call `pool.addLiquidity` directly with an arbitrary `owner`. The periphery `MetricOmmPoolLiquidityAdder` does call `_validateOwner(owner)`, but a direct call to the pool bypasses the periphery entirely. The attack is trivially repeatable with zero preconditions beyond knowing one allowlisted address. [5](#0-4) 

## Recommendation
Change `beforeAddLiquidity` to name and check `sender` (the actual depositor who pays tokens) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension.beforeSwap`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

## Proof of Concept
```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT allowlisted

Attack:
  bob calls pool.addLiquidity(
      owner    = alice,   // allowlisted → check passes
      salt     = 0,
      deltas   = <valid bins/shares>,
      callbackData = <bob's callback data>,
      extensionData = ""
  )

  Extension sees owner=alice → allowedDepositor[pool][alice]=true → no revert
  Pool calls bob's metricOmmModifyLiquidityCallback → bob pays tokens
  Alice receives LP shares; bob has deposited despite not being allowlisted

Result: deposit allowlist bypassed; pool admin's access control is ineffective.
``` [6](#0-5)

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
