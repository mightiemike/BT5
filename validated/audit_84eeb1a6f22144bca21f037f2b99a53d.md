### Title
`DepositAllowlistExtension` gates on LP position `owner` instead of actual depositor `sender`, allowing any user to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller of `addLiquidity`) and instead checks the `owner` argument (the LP position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` does not enforce `msg.sender == owner`, any unprivileged user can bypass the deposit gate by supplying any allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is declared as:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The first positional parameter — `sender`, the address that actually called `pool.addLiquidity` — is unnamed and ignored. The allowlist lookup uses `owner` instead.

The pool calls the hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

where `msg.sender` is the actual depositor and `owner` is the LP position recipient supplied by the caller. Crucially, `addLiquidity` imposes **no** `msg.sender == owner` constraint: [3](#0-2) 

(contrast `removeLiquidity`, which does enforce `if (msg.sender != owner) revert NotPositionOwner()`) [4](#0-3) 

The contract's own NatDoc states: *"Gates `addLiquidity` by depositor address, per pool."* The implementation contradicts this — it gates by LP-position-owner address, not by depositor address.

The bypass is also reachable through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)`, which accepts an arbitrary `owner` and only validates `owner != address(0)`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

The deposit allowlist is completely ineffective. Any unprivileged user can deposit into a restricted pool by passing any allowlisted address as `owner`. The LP shares are minted to that allowlisted address (not the attacker), so the attacker forfeits their deposited tokens — but the pool admin's access-control invariant is broken. Pools deployed with `DepositAllowlistExtension` for regulatory, KYC, or business-logic reasons cannot enforce their intended depositor restrictions. This is an admin-boundary break: an unprivileged path bypasses a pool-admin-configured guard.

---

### Likelihood Explanation

High. The allowlist state is public on-chain (`allowedDepositor` is a public mapping). Any observer can read at least one allowlisted address and immediately call `pool.addLiquidity(allowlistedAddress, ...)` to bypass the gate. No special privileges, flash loans, or oracle manipulation are required.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP position recipient):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` as a `beforeAddLiquidity` extension.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted to deposit.
3. `bob` (not allowlisted) observes `alice`'s address on-chain.
4. `bob` calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)` directly.
5. The pool calls `extension.beforeAddLiquidity(bob, alice, ...)`. The hook checks `allowedDepositor[pool][alice]` → `true`. No revert.
6. `LiquidityLib.addLiquidity` executes: tokens are pulled from `bob` via callback, LP shares are minted to `alice`.
7. `bob` has deposited into a pool he is not permitted to access. The allowlist is bypassed.

The same bypass works through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, alice, ...)` with `bob` as `msg.sender`. [7](#0-6) [8](#0-7) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
