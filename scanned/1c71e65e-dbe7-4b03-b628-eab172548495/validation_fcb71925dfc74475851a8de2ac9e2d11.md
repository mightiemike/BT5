### Title
`DepositAllowlistExtension` Checks LP Position `owner` Instead of Actual `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is documented to gate deposits by **depositor address**, but it silently discards the `sender` parameter and checks the `owner` parameter instead. Because `MetricOmmPool.addLiquidity` accepts a caller-controlled `owner` argument with no requirement that `owner == msg.sender`, any unprivileged address can bypass the allowlist by naming an allowlisted address as the LP position recipient.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct identity fields:

- `sender` = `msg.sender` — the actual caller who provides tokens via the swap callback
- `owner` = a freely-chosen argument — the address that receives the LP position shares [1](#0-0) 

There is no `require(msg.sender == owner)` guard in `addLiquidity` (contrast with `removeLiquidity`, which does enforce `msg.sender != owner → revert NotPositionOwner()`). [2](#0-1) 

The pool then calls the extension with both fields: [3](#0-2) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and ignored. The guard reads only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [4](#0-3) 

The contract's own NatSpec states: *"Gates `addLiquidity` by depositor address, per pool."* The depositor is `sender` (`msg.sender` of the pool call), not `owner`. The `SwapAllowlistExtension` correctly checks `sender` for swaps, confirming the deposit extension is inconsistent: [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may inject liquidity (e.g., KYC/compliance gating, curated LP sets). Because the guard checks the wrong identity field, it is completely ineffective: any address not on the allowlist can deposit tokens into the restricted pool by specifying any allowlisted address as `owner`. The pool admin's configured access boundary is bypassed by an unprivileged path, satisfying the **admin-boundary break** impact class.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a publicly-known allowlisted address as `owner`. No special privileges, flash loans, or oracle manipulation are needed. The allowlisted addresses are discoverable on-chain via the `allowedDepositor` mapping. Likelihood is **High**.

---

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `Alice`.
2. `Bob` (not allowlisted) calls `pool.addLiquidity(owner = Alice, salt = 0, deltas = ..., ...)`.
3. The pool invokes `DepositAllowlistExtension.beforeAddLiquidity(sender = Bob, owner = Alice, ...)`.
4. The guard evaluates `allowedDepositor[pool][Alice]` → `true` → **no revert**.
5. The pool calls `Bob.metricOmmSwapCallback(...)` to pull tokens from Bob.
6. Alice receives the LP position shares; Bob has successfully deposited into a pool he is not authorized to access.
7. The pool admin's allowlist restriction is entirely bypassed. [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-195)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L204-206)
```text
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
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
