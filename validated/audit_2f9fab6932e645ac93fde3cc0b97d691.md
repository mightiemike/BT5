Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Position Beneficiary (`owner`) Instead of Actual Depositor (`sender`), Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` who pays for the deposit) and instead validates the caller-supplied `owner` argument (the position beneficiary). Because `owner` is a free argument any caller can set to any already-allowlisted address, the deposit allowlist is fully bypassed by any unprivileged address. The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender`, confirming the asymmetry is a defect.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the position beneficiary into the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter (the actual depositor/`sender`) as unnamed, discarding it entirely, and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  ...
}
``` [2](#0-1) 

Since `owner` is a free caller-supplied argument, any address can pass the guard by naming any already-allowlisted address as `owner`. The `SwapAllowlistExtension` correctly checks `sender`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  ...
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) { ... }
}
``` [3](#0-2) 

The exploit path is:
1. Attacker (Bob) identifies any allowlisted address `Alice` via `AllowedToDepositSet` events or direct storage reads.
2. Bob calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
3. The extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
4. Bob's tokens are pulled via `metricOmmSwapCallback` on `msg.sender` (Bob).
5. Alice receives the position shares.

`removeLiquidity` enforces `msg.sender != owner` revert, so Bob cannot directly recover the funds: [4](#0-3) 

Alice can call `removeLiquidity` to collect Bob's deposited tokens. The pool's access-control invariant is broken: unauthorized liquidity alters bin balances, share totals, and oracle stop-loss watermarks for all existing LPs.

## Impact Explanation

The deposit allowlist — a pool admin-configured access control intended to gate which principals may inject liquidity (e.g., KYC/compliance, LP-tier restrictions, protocol-controlled bootstrapping) — is completely neutralized. Any unprivileged EOA or contract can deposit into a supposedly restricted pool. The attacker's principal is transferred to the allowlisted address's position (direct loss of depositor funds), and unauthorized liquidity enters the pool, corrupting `binTotals.scaledToken0`/`scaledToken1`, `_binTotalShares`, and `_positionBinShares` for all existing LPs. This constitutes both direct loss of user principal and broken core pool functionality, meeting the High severity threshold. [5](#0-4) 

## Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no complex setup — only the ability to call `addLiquidity` on the pool (any EOA or contract). Allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` storage reads. The attack is repeatable at any time and by any actor. Likelihood is **High**. [6](#0-5) 

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor/caller) instead of `owner`, mirroring `SwapAllowlistExtension.beforeSwap`:

```solidity
// Fixed
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

Also update `isAllowedToDeposit` and `setAllowedToDeposit` documentation/semantics to reflect that the checked address is the depositor/caller, not the position beneficiary. [7](#0-6) 

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][Alice] = true   // Alice is allowlisted
  Bob is NOT allowlisted

Attack:
  Bob calls pool.addLiquidity(
    owner        = Alice,   // allowlisted — passes the guard
    salt         = 0,
    deltas       = <desired bins/shares>,
    callbackData = <Bob pays token0/token1 in the callback>,
    extensionData = ""
  )

Trace:
  MetricOmmPool.addLiquidity:
    _beforeAddLiquidity(msg.sender=Bob, owner=Alice, ...)
      → DepositAllowlistExtension.beforeAddLiquidity(Bob /*discarded*/, Alice, ...)
      → allowedDepositor[pool][Alice] == true → no revert ✓
    LiquidityLib.addLiquidity(..., owner=Alice, ...)
      → metricOmmSwapCallback called on Bob → Bob's tokens transferred to pool
      → Alice's position shares credited

Result:
  - Bob's tokens are locked in Alice's position
  - Alice calls removeLiquidity to collect Bob's tokens
  - Deposit allowlist fully bypassed; unauthorized liquidity in pool
  - binTotals, _binTotalShares, _positionBinShares corrupted for all LPs
``` [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-29)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
