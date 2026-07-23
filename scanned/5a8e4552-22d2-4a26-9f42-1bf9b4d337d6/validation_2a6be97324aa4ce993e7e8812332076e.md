### Title
`DepositAllowlistExtension` checks LP position `owner` instead of actual depositor `sender`, allowing unauthorized deposits — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that actually calls `addLiquidity` and pays tokens) and instead checks the `owner` argument (the LP position holder) against the allowlist. Because `addLiquidity` imposes no requirement that `msg.sender == owner`, any unauthorized address can bypass the deposit gate by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension verbatim: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional parameter (`sender`) is unnamed and discarded; the guard is evaluated only against `owner`: [3](#0-2) 

`addLiquidity` contains no `msg.sender == owner` check, so any caller may freely choose any `owner`: [4](#0-3) 

By contrast, `removeLiquidity` does enforce `msg.sender == owner`: [5](#0-4) 

And `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller), not `recipient`: [6](#0-5) 

The asymmetry between the two allowlist extensions confirms the deposit extension is checking the wrong actor.

---

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to restrict which addresses may add liquidity (e.g., for regulatory compliance, KYC gating, or preventing adversarial LPs). With the guard evaluating `owner` instead of `sender`:

- Any address not on the allowlist can add liquidity to a restricted pool by specifying any allowlisted address as `owner`.
- Pool bin state (`binTotals`, `_binStates`, `_binTotalShares`, `_positionBinShares`) is modified by an unauthorized actor.
- The pool receives tokens from an unauthorized source, violating the admin-configured access boundary.
- The allowlisted `owner` receives an LP position they did not initiate; they can withdraw it, but the unauthorized deposit has already altered pool state.

This is an admin-boundary break: an unprivileged path bypasses a pool-admin-configured guard with direct effect on pool liquidity state.

---

### Likelihood Explanation

Exploitation requires only knowing one allowlisted address, which is observable on-chain from `AllowedToDepositSet` events or prior `addLiquidity` calls. No special privilege, flash loan, or oracle manipulation is needed. Any EOA or contract can execute the bypass in a single transaction.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP position holder), mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
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

If the intended semantics are to gate the LP position holder (not the depositor), the extension name, event names, and setter names should be updated to reflect that, and the pool documentation should clarify that anyone can deposit on behalf of an allowlisted owner.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` attached to `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin calls `setAllowedToDeposit(pool, allowedAddress, true)`. Attacker (`attackerAddress`) is not on the allowlist.
3. Attacker calls:
   ```solidity
   pool.addLiquidity(
       allowedAddress,   // owner — on the allowlist
       salt,
       deltas,
       callbackData,
       extensionData
   );
   ```
4. `_beforeAddLiquidity(msg.sender=attackerAddress, owner=allowedAddress, ...)` is dispatched.
5. Extension evaluates `allowedDepositor[pool][allowedAddress]` → `true` → hook returns selector, no revert.
6. `LiquidityLib.addLiquidity` mints LP shares keyed to `allowedAddress`.
7. The swap callback fires on `attackerAddress`, pulling tokens from the attacker into the pool.
8. Attacker has added liquidity to a restricted pool; the deposit allowlist is fully bypassed.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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
