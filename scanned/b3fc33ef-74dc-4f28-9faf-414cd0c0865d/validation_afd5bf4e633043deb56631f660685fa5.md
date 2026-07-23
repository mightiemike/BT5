### Title
DepositAllowlistExtension Guards LP Position Owner Instead of Actual Depositor, Allowing Full Allowlist Bypass — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of the `addLiquidity` call — the entity that pays tokens) and gates only on `owner` (the LP position owner, a free caller-supplied parameter). Because `owner` is an arbitrary address chosen by the caller in `MetricOmmPool.addLiquidity`, any unprivileged address can bypass the deposit allowlist by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

- `sender` = `msg.sender` — the actual caller who will pay tokens via the `metricOmmModifyLiquidityCallback`
- `owner` = a free parameter the caller sets to any address; the LP position is minted to this address

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (unnamed first parameter) and checks only `owner`:

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

The NatSpec for the contract states *"Gates `addLiquidity` by depositor address"*, but the depositor (the entity paying tokens) is `sender`, not `owner`. [3](#0-2) 

The contrast with `SwapAllowlistExtension.beforeSwap` makes the discrepancy clear — the swap guard correctly checks `sender`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

---

### Impact Explanation

The deposit allowlist — an admin-configured guard intended to restrict which addresses may supply liquidity — is fully bypassed. Any unprivileged address can call:

```
pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)
```

The hook passes because `owner = allowlistedAddress` is allowlisted. The LP position is minted to `allowlistedAddress`; tokens are pulled from the actual caller via callback. The pool admin's access-control boundary is broken: actors the admin explicitly excluded from depositing can deposit freely. Additionally, the allowlisted address receives LP shares it did not initiate, which can corrupt the internal accounting of any smart-contract LP that tracks its own position size.

---

### Likelihood Explanation

No special privileges are required. Any address can call `addLiquidity` on the pool with `owner` set to any allowlisted address. The allowlisted address is publicly readable from `allowedDepositor` or discoverable from past transactions. The attack requires only knowledge of one allowlisted address and sufficient token balance to fund the callback.

---

### Recommendation

Check `sender` (the actual depositor/payer) instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached to `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. `bob` (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. The pool calls `extension.beforeAddLiquidity(bob, alice, salt, deltas, "")`.
5. The hook checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` mints LP shares to `alice`; tokens are pulled from `bob` via callback.
7. `bob` has deposited into the pool despite being explicitly excluded by the pool admin. [2](#0-1) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
