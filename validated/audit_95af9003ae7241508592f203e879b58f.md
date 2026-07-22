### Title
`DepositAllowlistExtension` checks position `owner` instead of actual depositor `sender`, allowing deposit allowlist bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` parameter (the address that actually calls `addLiquidity` and pays tokens) and instead gates on the `owner` parameter (the LP position recipient). Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address with no requirement that it equals `msg.sender`, any un-allowlisted address can bypass the deposit guard by naming an allowlisted address as the position owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards them as `(sender, owner, ...)`: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender` — the actual caller/payer) is **unnamed and discarded**. The allowlist check is performed exclusively on `owner`:

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

The NatDoc, mapping name (`allowedDepositor`), and setter name (`setAllowedToDeposit`) all express the intent to restrict by **depositor** (the paying caller), not by position owner: [4](#0-3) 

`MetricOmmPool.addLiquidity` imposes no constraint that `owner == msg.sender`: [5](#0-4) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the primary periphery entry point) accepts a caller-supplied `owner` and only validates it is non-zero: [6](#0-5) 

The payer stored in transient context is always `msg.sender` (the un-allowlisted caller), while `positionOwner` (the allowlisted address) is passed to the pool: [7](#0-6) 

---

### Impact Explanation

An un-allowlisted address can deposit tokens into a pool that the admin intended to restrict, by naming any allowlisted address as `owner`. The un-allowlisted address pays the tokens (via the `metricOmmModifyLiquidityCallback`); the allowlisted address receives the LP position and can later withdraw. When the two addresses are controlled by the same entity (or cooperating parties), the deposit allowlist is fully circumvented. This is an admin-boundary break: an unprivileged path bypasses an admin-configured guard, violating the pool's access-control invariant and any associated compliance requirements.

---

### Likelihood Explanation

Exploitation requires only that the attacker knows one allowlisted address (readable from the public `allowedDepositor` mapping) and deploys a contract implementing `metricOmmModifyLiquidityCallback`. No privileged role, oracle manipulation, or non-standard token is needed. The bypass is deterministic and repeatable.

---

### Recommendation

Check `sender` (the actual caller/payer) instead of `owner` in `beforeAddLiquidity`:

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

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `alice`.
2. `bob` (not allowlisted) deploys an attacker contract that implements `metricOmmModifyLiquidityCallback` and calls:
   ```solidity
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
3. The pool calls `_beforeAddLiquidity(bob, alice, ...)`.
4. `DepositAllowlistExtension` evaluates `allowedDepositor[pool][alice]` → `true` → hook passes.
5. Liquidity is added; the pool calls `bob.metricOmmModifyLiquidityCallback(...)` and `bob` pays the tokens.
6. `alice` receives the LP position; `bob` has deposited into a pool from which it was explicitly excluded.
7. If `bob` and `alice` are the same entity (different addresses), `alice` withdraws the tokens — the allowlist is fully bypassed with no residual trace on `bob`. [3](#0-2) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-19)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```
