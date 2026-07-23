### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unauthorized Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. However, its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller) and checks only the `owner` argument (the LP position recipient). Any unauthorized address can bypass the allowlist by calling `pool.addLiquidity(authorized_owner, …)`, causing unauthorized tokens to enter a restricted pool.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

where `msg.sender` is the actual depositor and `owner` is the LP position recipient.

`DepositAllowlistExtension.beforeAddLiquidity` receives both but ignores the first:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first parameter (`sender`) is unnamed and never read. The guard only checks whether `owner` is on the allowlist. Because `owner` is a caller-supplied argument, any unauthorized address can pass an authorized owner's address and satisfy the check. The pool then executes the callback on `msg.sender` (the unauthorized caller), pulling their tokens into the pool, while LP shares are credited to the authorized `owner`.

The contract's own NatSpec states the intent: *"Gates `addLiquidity` by depositor address, per pool."* The implementation contradicts this: it gates by LP-position-owner address, not by depositor address. [1](#0-0) 

The pool passes `msg.sender` as the first argument to the hook: [2](#0-1) 

The hook discards it and checks `owner` instead: [1](#0-0) 

---

### Impact Explanation

The deposit allowlist is completely ineffective as an access control on who can inject tokens into the pool. Any unprivileged address can:

1. Deposit arbitrary token amounts into a pool that the admin intended to be restricted.
2. Force LP shares onto an authorized owner without their consent (the authorized owner must then actively remove liquidity to recover the tokens).
3. Alter pool reserves and bin balances from an unauthorized source, potentially affecting oracle-anchored pricing, bin position, and the value metrics tracked by `OracleValueStopLossExtension`.

This is a broken core pool functionality: the admin-configured allowlist guard is bypassed by an unprivileged path, matching the "Admin-boundary break" impact category.

---

### Likelihood Explanation

Exploitation requires no special privileges, no flash loan, and no complex setup. Any EOA or contract can call `pool.addLiquidity(authorized_owner, …)` with a valid authorized owner address (which is public on-chain via `allowedDepositor` mapping). The bypass is deterministic and repeatable.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` in `beforeAddLiquidity`:

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

If the intent is to restrict both the depositor and the LP position owner, both should be checked.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured in `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is authorized.
3. `bob` (unauthorized) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `_beforeAddLiquidity(bob, alice, …)` → extension receives `sender=bob`, `owner=alice`.
5. Extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` executes: callback fires on `bob`, pulling `bob`'s tokens into the pool; LP shares are credited to `alice`.
7. `bob`'s tokens are now inside the restricted pool despite `bob` never being authorized. [3](#0-2) [4](#0-3)

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
