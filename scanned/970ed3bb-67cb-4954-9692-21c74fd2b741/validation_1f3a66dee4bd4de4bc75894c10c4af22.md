### Title
`DepositAllowlistExtension` Gates on `owner` Instead of `sender`, Allowing Any Unauthorized Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` parameter (the position recipient) rather than `sender` (the actual depositor/payer). Because `owner` is a free caller-supplied argument to `MetricOmmPool.addLiquidity`, any address not on the allowlist can bypass the deposit gate by setting `owner` to any address that is already allowed.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct actor addresses:

- `msg.sender` → forwarded to the extension as `sender` (the actual caller who provides tokens via callback)
- `owner` → a caller-supplied parameter forwarded as `owner` (the position recipient) [1](#0-0) 

The pool enforces `msg.sender == owner` only for `removeLiquidity`, not for `addLiquidity`. Any address may call `addLiquidity` with an arbitrary `owner`. [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and checks only `owner`:

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

By contrast, `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual caller):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The two extensions are architecturally inconsistent: the swap guard correctly identifies the economic actor (`sender`), while the deposit guard checks the wrong actor (`owner`).

---

### Impact Explanation

A pool admin who deploys a curated pool with `DepositAllowlistExtension` to restrict liquidity provision to KYC'd or trusted addresses finds the control is completely ineffective. Any unauthorized address can:

1. Call `pool.addLiquidity(allowedAddress, salt, deltas, callbackData, extensionData)` with `owner` set to any address already on the allowlist.
2. The extension checks `allowedDepositor[pool][allowedAddress]` → passes.
3. The unauthorized caller provides tokens via the liquidity callback and a position is minted for `allowedAddress`.

If the unauthorized depositor and the allowed address collude (or are the same entity using a second wallet), the allowed address can call `removeLiquidity` to recover the tokens, effectively laundering unauthorized liquidity through the curated pool. Even without collusion, the allowlist policy is broken: the pool admin cannot prevent arbitrary addresses from injecting liquidity into the pool.

This breaks the core functionality of the `DepositAllowlistExtension` and constitutes an admin-boundary break where an unprivileged path bypasses a pool-admin-configured access control.

---

### Likelihood Explanation

The bypass requires only a single direct call to `pool.addLiquidity` with `owner` set to any known allowed address. No special privileges, flash loans, or complex setup are needed. The allowed address list is public on-chain (via `allowedDepositor` mapping). Any motivated actor can trivially identify an allowed address and execute the bypass.

---

### Recommendation

Change `beforeAddLiquidity` to gate on `sender` (the actual depositor/payer) rather than `owner` (the position recipient), consistent with how `SwapAllowlistExtension` gates on `sender`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

If the intended policy is to restrict who can *own* a position (rather than who can *deposit*), the parameter name and documentation should be updated to make this explicit, and the `isAllowedToDeposit` view function should be renamed accordingly to avoid misleading pool admins.

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension
  - allowedDepositor[pool][alice] = true
  - bob is NOT on the allowlist

Attack:
  1. bob calls pool.addLiquidity(alice, salt, deltas, callbackData, "")
  2. Extension checks allowedDepositor[pool][alice] → true → passes
  3. Pool pulls tokens from bob via callback
  4. Position minted for alice
  5. alice calls pool.removeLiquidity(alice, salt, deltas, "") → receives tokens
  6. alice and bob split proceeds off-chain

Result: bob bypassed the deposit allowlist entirely.
``` [3](#0-2) [5](#0-4)

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
