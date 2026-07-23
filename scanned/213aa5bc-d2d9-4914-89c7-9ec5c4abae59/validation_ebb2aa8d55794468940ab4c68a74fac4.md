### Title
Deposit Allowlist Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Callers to Bypass the Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates on `owner` instead. Because `MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address, any non-allowlisted address can pass the gate by nominating an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address parameters: `sender` (the actual `msg.sender` of the pool call) and `owner` (the position recipient). The first parameter is unnamed and ignored; the allowlist check is performed only on `owner`: [1](#0-0) 

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`: [2](#0-1) 

Because `owner` is a free parameter in the public `addLiquidity` signature, any caller can supply an allowlisted address as `owner`. The extension then evaluates `allowedDepositor[pool][allowlisted_address]` which is `true`, and the gate passes unconditionally for the non-allowlisted caller. [3](#0-2) 

After the gate passes, `LiquidityLib.addLiquidity` records the position under `owner` and calls the liquidity callback on `msg.sender` (the attacker) to pull tokens: [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to restrict which addresses may add liquidity to a curated pool. The invariant is: **only allowlisted addresses may act as the depositing party**. By checking `owner` instead of `sender`, the invariant is broken: any address can deposit into a restricted pool by nominating an allowlisted address as the position owner. The non-allowlisted caller pays the tokens; the allowlisted address receives the LP position without consent. This constitutes an admin-boundary break — an unprivileged path bypasses an admin-configured access control.

---

### Likelihood Explanation

The bypass requires only a direct call to the public `pool.addLiquidity` function with an allowlisted address as `owner`. No special privileges, flash loans, or complex setup are needed. Any allowlisted address is publicly discoverable via the `AllowedToDepositSet` event or `allowedDepositor` view. Likelihood is high.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositing party) rather than `owner`:

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

```solidity
// attacker is NOT in the allowlist; allowlisted is.
depositExtension.setAllowedToDeposit(address(pool), allowlisted, true);

// Attacker calls addLiquidity directly, nominating the allowlisted address as owner.
// Extension checks allowedDepositor[pool][allowlisted] == true → passes.
// Attacker pays tokens via callback; LP shares credited to `allowlisted`.
vm.prank(attacker);
pool.addLiquidity(allowlisted, salt, deltas, callbackData, "");

// Assert: attacker's tokens were pulled, allowlisted received shares.
assertGt(positionBinShares(pool, allowlisted, salt, bin), 0);
```

The `removeLiquidity` path enforces `msg.sender == owner`, so the attacker cannot recover the deposited tokens — they are permanently transferred to the allowlisted address's position. [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```
