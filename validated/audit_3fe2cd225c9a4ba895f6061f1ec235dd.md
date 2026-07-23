Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Recipient (`owner`) Instead of Actual Depositor (`sender`), Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the address that calls `addLiquidity` and pays tokens) and instead validates `owner` (a free caller-supplied argument designating the LP share recipient). Because `owner` is attacker-controlled, any address not on the allowlist can pass the guard by naming any allowlisted address as `owner`, rendering the deposit access control completely inoperative.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP recipient into `_beforeAddLiquidity`:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` correctly encodes both into the hook call:

```solidity
// ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

However, `DepositAllowlistExtension.beforeAddLiquidity` drops `sender` entirely (unnamed first parameter) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The contract's own NatSpec states it "Gates `addLiquidity` by depositor address," but the implementation gates by LP-position recipient address. The correct pattern is demonstrated by `SwapAllowlistExtension.beforeSwap`, which reads `sender` and ignores the second parameter:

```solidity
// SwapAllowlistExtension.sol L31-40
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Exploit path:
1. Pool admin deploys pool with `DepositAllowlistExtension` and sets `allowedDepositor[pool][trustedLP] = true`.
2. Attacker (not on allowlist) calls `pool.addLiquidity(owner=trustedLP, salt, deltas, callbackData, "")`.
3. Hook receives `(sender=attacker, owner=trustedLP, ...)`, checks `allowedDepositor[pool][trustedLP]` → `true` → guard passes.
4. Attacker pays tokens via `metricOmmSwapCallback`; LP shares are minted to `trustedLP`.
5. Attacker repeats across arbitrary bins to manipulate `curPosInBin`/`curBinIdx` and extract value from existing LPs via subsequent swaps at distorted prices.

No existing guard prevents this: `addLiquidity` has no caller restriction beyond the extension hook, and the hook itself is the broken control.

## Impact Explanation
The deposit allowlist guard is fully bypassed by any unprivileged EOA or contract. This constitutes an **admin-boundary break**: a pool-admin-configured access control (KYC gate, whitelist-only pool, manipulation-resistant pool) is circumvented by an unprivileged path. Secondary impact includes pool-state manipulation — an attacker can shift `curPosInBin`/`curBinIdx` by adding liquidity at chosen bins, then swap at the distorted price to extract value from existing LPs, constituting a direct loss of LP principal.

## Likelihood Explanation
Exploitation requires no special privilege, no flash loan, and no complex setup. The allowlisted addresses are publicly readable from `allowedDepositor`. Any EOA can call `pool.addLiquidity(trustedLP, ...)` directly. The attack is repeatable and stateless.

## Recommendation
Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// AFTER (correct)
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
```
Setup:
  pool  = MetricOmmPool with DepositAllowlistExtension configured
  admin: allowedDepositor[pool][trustedLP] = true
  attacker: NOT in allowedDepositor

Attack:
  attacker calls pool.addLiquidity(
      owner        = trustedLP,   // allowlisted address — passes the broken guard
      salt         = 0,
      deltas       = <chosen bins>,
      callbackData = <attacker pays tokens here>,
      extensionData= ""
  )

Hook execution:
  beforeAddLiquidity(sender=attacker, owner=trustedLP, ...)
  → checks allowedDepositor[pool][trustedLP] == true  ✓ guard passes

Result:
  - attacker deposits into a restricted pool without being allowlisted
  - LP shares minted to trustedLP (attacker paid, trustedLP receives unwanted position)
  - attacker can shift curPosInBin/curBinIdx across bins and subsequently
    swap at the distorted price to extract value from existing LPs
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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
