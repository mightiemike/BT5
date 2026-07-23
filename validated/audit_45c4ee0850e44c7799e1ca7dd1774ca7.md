### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller who provides tokens) and instead validates `owner` (the LP-position recipient). Any unprivileged address can bypass the allowlist by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with two distinct addresses:

- `sender` = `msg.sender` of `addLiquidity` — the address that provides tokens via the swap callback
- `owner` = the caller-supplied LP-position owner [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then silently drops `sender` (unnamed `address`) and gates on `owner` instead: [3](#0-2) 

The contract's own `isAllowedToDeposit` view names the second argument `depositor`, confirming the design intent is to gate the token-providing caller, not the position recipient: [4](#0-3) 

Because `owner` is a free caller-supplied parameter with no other validation, any address can pass the allowlist check by supplying any allowlisted address as `owner`.

---

### Impact Explanation

The deposit allowlist — the pool admin's primary mechanism for restricting who may provide liquidity — is completely ineffective. An unprivileged address can deposit into a pool that is supposed to be restricted, breaking the admin-boundary invariant. This qualifies under **"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path"** and **"Broken core pool functionality"**.

---

### Likelihood Explanation

Exploitation requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with `owner = any_allowlisted_address`. The allowlist provides zero protection in practice.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and sets `allowedDepositor[pool][alice] = true`. Bob is **not** allowlisted.
2. Bob calls `pool.addLiquidity(owner = alice, salt = 0, deltas = ..., callbackData = ..., extensionData = ...)`.
3. Pool calls `extension.beforeAddLiquidity(sender = bob, owner = alice, ...)`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. Bob's callback transfers tokens into the pool; the LP position is credited to `alice`.
6. Bob has deposited into a restricted pool without being on the allowlist. The allowlist guard is fully bypassed. [3](#0-2) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-30)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
