### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." However, its `beforeAddLiquidity` hook checks the **position owner** (`owner`) rather than the **actual caller** (`sender`). Because `owner` is a free parameter supplied by the caller, any unprivileged address can bypass the allowlist by setting `owner` to any address that is already allowlisted.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with both the real caller and the chosen owner:

```solidity
// MetricOmmPool.sol – addLiquidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both arguments faithfully:

```solidity
// ExtensionCalling.sol – _beforeAddLiquidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

The extension receives `(address sender, address owner, …)`. The guard, however, silently discards `sender` and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol – beforeAddLiquidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` inside the extension is the pool (the caller of the hook), so `allowedDepositor[pool][owner]` is evaluated. Because `owner` is an arbitrary address chosen by the real caller, any actor can pass `owner = any_allowlisted_address` and the check succeeds, regardless of who is actually sending tokens.

Compare with `SwapAllowlistExtension`, which correctly checks `sender` (the first parameter, the real caller):

```solidity
// SwapAllowlistExtension.sol – beforeSwap
function beforeSwap(address sender, address, …) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    …
}
```

The asymmetry confirms the deposit guard is checking the wrong address.

---

### Impact Explanation

The deposit allowlist is completely ineffective. Any address — including addresses the pool admin explicitly excluded — can deposit tokens into a restricted pool by supplying `owner = any_allowlisted_address`. The pool admin's access-control boundary is bypassed by an unprivileged path, satisfying the **admin-boundary break** impact gate. Additionally, the unauthorized depositor forces LP shares onto the allowlisted owner without their consent, which can expose that owner to unwanted pool risk.

---

### Likelihood Explanation

Exploitation requires only a single `addLiquidity` call with `owner` set to any address that appears in `allowedDepositor`. No special privileges, flash loans, or multi-step setup are needed. The allowlisted address is publicly readable from the mapping. Likelihood is **High**.

---

### Recommendation

Replace the ignored first parameter with `sender` and check it instead of `owner`:

```solidity
// Before (buggy)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}

// After (fixed)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `Alice`.
2. `Bob` (not allowlisted) calls `pool.addLiquidity(owner = Alice, …)`.
3. The extension evaluates `allowedDepositor[pool][Alice]` → `true`.
4. The hook returns without reverting; `Bob`'s tokens are deposited and LP shares are minted to `Alice`.
5. `Bob` has successfully deposited into a pool he was explicitly excluded from. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
