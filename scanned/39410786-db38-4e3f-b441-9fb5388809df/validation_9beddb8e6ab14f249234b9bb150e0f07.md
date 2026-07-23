### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and gates on `owner` (the position recipient) instead. Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any non-allowlisted address can deposit into a guarded pool by nominating an allowlisted address as `owner`. The guard is structurally misbound to the wrong actor.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments: the first is `sender` (the real caller of `addLiquidity`), the second is `owner` (the position recipient). The implementation discards `sender` and checks `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as the second argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`addLiquidity` has no requirement that `msg.sender == owner`: [3](#0-2) 

The sister extension `SwapAllowlistExtension` correctly checks `sender` (the real caller), not `recipient`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The asymmetry is the defect: `SwapAllowlistExtension` gates on the actor who initiates the action; `DepositAllowlistExtension` gates on the beneficiary instead.

The periphery router `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` explicitly supports `owner ≠ msg.sender`: [5](#0-4) 

When this path is used, the pool receives `sender = LiquidityAdder` and `owner = caller-supplied address`. The extension checks `owner`, so any allowlisted address used as `owner` passes the guard regardless of who the real depositor is.

---

### Impact Explanation

The deposit allowlist guard is rendered ineffective. A non-allowlisted address can:

1. Call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` directly, **or**
2. Call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedAddress, ...)`.

In both cases the extension evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` and passes. The non-allowlisted address pays the tokens via the modify-liquidity callback; the allowlisted address receives the LP shares. The pool admin's access control over who may provide liquidity is completely bypassed. This is an admin-boundary break: an unprivileged path circumvents a pool-admin-configured guard.

---

### Likelihood Explanation

Exploitation requires no special privileges. Any address that is not on the allowlist can execute the bypass in a single transaction, provided it knows at least one allowlisted address (which is observable on-chain via `AllowedToDepositSet` events or `allowedDepositor` view calls). The attack surface is present on every pool that deploys `DepositAllowlistExtension` with a non-trivial allowlist.

---

### Recommendation

Mirror the pattern used by `SwapAllowlistExtension`: gate on `sender` (the actual caller of `addLiquidity`), not `owner`.

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

Also update `setAllowedToDeposit` / `isAllowedToDeposit` documentation to clarify that the allowlisted address is the caller of `addLiquidity`, not the position owner.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  pool admin calls: depositExtension.setAllowedToDeposit(pool, alice, true)
  bob is NOT in the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner   = alice,   // allowlisted
      salt    = 0,
      deltas  = <valid bins/shares>,
      callbackData = ...,
      extensionData = ""
  )

Extension evaluation (inside _beforeAddLiquidity):
  sender arg = bob   (discarded — first param is `address,`)
  owner  arg = alice
  check: allowedDepositor[pool][alice] == true  → PASSES

Result:
  bob's tokens are pulled via metricOmmModifyLiquidityCallback
  alice receives LP shares
  bob has deposited into a pool that explicitly excluded him
  pool admin's allowlist is bypassed
```

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
