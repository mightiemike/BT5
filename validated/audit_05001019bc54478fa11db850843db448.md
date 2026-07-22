### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` argument and checks only `owner`. Because `owner` (the LP-position recipient) and `sender` (the address that calls `addLiquidity` and pays tokens via callback) are independent parameters, any unauthorized address can bypass the allowlist by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and passes both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The extension hook receives `sender` as its first argument but discards it (unnamed `address,`), then checks only `owner`:

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

The `SwapAllowlistExtension` correctly checks `sender` (the actual swapper):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

The asymmetry is the root cause: `SwapAllowlistExtension` gates by `sender`; `DepositAllowlistExtension` gates by `owner`.

---

### Impact Explanation

**Allowlist guard is fully bypassed for the actual depositor.** Any address not on the allowlist can call `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)` directly (bypassing the periphery `LiquidityAdder`). The extension sees `owner = allowlistedAddress`, passes the check, and the unauthorized caller pays tokens via the modify-liquidity callback. The pool admin's configured access control is rendered ineffective.

Secondary impact: a legitimately allowlisted `sender` who specifies a non-allowlisted `owner` is incorrectly blocked, breaking the expected deposit flow for that user.

This matches the **admin-boundary break** allowed impact: a pool admin configures a guard (deposit allowlist) that is bypassed by an unprivileged path (direct pool call with an allowlisted `owner`).

---

### Likelihood Explanation

- The pool's `addLiquidity` function imposes no restriction on who can be `owner`; any caller can supply any `owner` address.
- The periphery `MetricOmmPoolLiquidityAdder` does call `_validateOwner(owner)`, but a direct call to the pool bypasses the periphery entirely.
- No special privilege is required; any EOA or contract can execute the bypass. [4](#0-3) 

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor who pays tokens) instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`. [2](#0-1) 

---

### Proof of Concept

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
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
  }

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-40)
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
```
