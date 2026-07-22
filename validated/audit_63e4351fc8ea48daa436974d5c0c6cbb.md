### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual `msg.sender` of `pool.addLiquidity`) and instead checks the `owner` argument (the position owner supplied by the caller). Because `owner` is a free caller-controlled parameter with no identity binding, any unprivileged address can bypass the allowlist by specifying an allowlisted `owner` address, rendering the access-control gate completely ineffective.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address and passes both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both addresses to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first `address` parameter but silently discards it (unnamed), then checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The analogous `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller), not `recipient`:

```solidity
function beforeSwap(address sender, address, ...)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The naming convention in `DepositAllowlistExtension` — `setAllowedToDeposit(address pool_, address depositor, ...)` and `isAllowedToDeposit(address pool_, address depositor)` — confirms the intent is to gate the depositing actor (the caller), not the position owner. [5](#0-4) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (with explicit owner) only validates `owner != address(0)`, placing no restriction on who the caller may name as `owner`:

```solidity
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);   // only checks owner != address(0)
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [6](#0-5) 

---

### Impact Explanation

Any unprivileged address can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` directly, or route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedAddress, ...)`. The extension hook passes because `owner` is allowlisted; the actual caller (`sender`) is never checked. The deposit allowlist — the pool admin's sole mechanism for restricting who may add liquidity — is completely neutralized. This is an admin-boundary break: an unprivileged path bypasses a pool-admin-configured access control gate.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no price manipulation. Any EOA or contract can call `addLiquidity` with a known allowlisted address as `owner`. The allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events. Likelihood is high.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

Also rename the storage mapping key from `depositor` to `caller` or `sender` throughout the contract and interface to make the intent unambiguous.

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension as beforeAddLiquidity hook.
  - Pool admin calls setAllowedToDeposit(pool, alice, true).
  - bob is NOT allowlisted.

Attack:
  1. bob calls pool.addLiquidity(alice, salt, deltas, callbackData, "")
     - _beforeAddLiquidity(bob, alice, ...) is dispatched.
     - Extension checks allowedDepositor[pool][alice] → true → passes.
     - bob's metricOmmModifyLiquidityCallback is invoked; bob pays tokens.
     - Position (alice, salt) is created in the pool.

Result:
  - bob (non-allowlisted) successfully deposited into a restricted pool.
  - The deposit allowlist is bypassed entirely.
  - Pool admin's access control is ineffective.
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
