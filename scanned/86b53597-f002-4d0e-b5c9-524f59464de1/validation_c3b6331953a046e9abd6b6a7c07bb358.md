### Title
`DepositAllowlistExtension` Gates on `owner` (LP Recipient) Instead of `sender` (Actual Payer), Allowing Unauthorized Depositors to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks whether the `owner` (position recipient) is allowlisted, but the pool's `addLiquidity` explicitly supports an operator pattern where `msg.sender` (the payer) need not equal `owner`. Any unauthorized address can bypass the deposit allowlist by calling `pool.addLiquidity(allowlisted_owner, ...)`, paying tokens into the pool while the allowlisted address receives the LP shares.

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the payer (who funds the deposit via callback); `owner` is the position recipient (who receives LP shares). The pool documentation explicitly states this is an intentional operator pattern:

> `msg.sender` pays but need not equal `owner` (operator pattern).

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and only checks `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Here `msg.sender` is the pool (the extension is called by the pool), so `allowedDepositor[msg.sender][owner]` resolves to `allowedDepositor[pool][owner]`. The check passes whenever `owner` is allowlisted, regardless of who is actually paying.

This is the direct analog to the external bug: just as `max_flashloan` failed to consult the `PSP22Capped` cap when both extensions were composed, `DepositAllowlistExtension` fails to consult the `sender` dimension of the allowlist when the operator pattern is used — the configured guard is misapplied to the wrong actor.

### Impact Explanation

An unauthorized address (not on the allowlist) can deposit tokens into a pool protected by `DepositAllowlistExtension` by specifying any allowlisted address as `owner`. The unauthorized address pays the tokens via the modify-liquidity callback; the allowlisted address receives the LP shares. The pool admin's configured access control is silently bypassed by any unprivileged caller. This breaks the admin-boundary invariant: a pool admin-configured allowlist is circumvented by an unprivileged path without any privileged action or malicious setup.

### Likelihood Explanation

The trigger requires only a standard `addLiquidity` call with a valid allowlisted `owner` address. No special permissions, flash loans, or reentrancy are needed. Any address that knows at least one allowlisted address (which is on-chain public state via `allowedDepositor`) can execute the bypass. The `MetricOmmPoolLiquidityAdder` periphery contract also calls `pool.addLiquidity(owner, ...)` with a caller-supplied `owner`, providing an additional entry path.

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual payer) rather than `owner` (the position recipient):

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the payer and the position recipient, both should be checked. The pool admin documentation should also clarify which actor the allowlist is intended to gate.

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin calls `extension.setAllowedToDeposit(pool, alice, true)` — Alice is allowlisted; Bob is not.
3. Bob (unauthorized) calls:
   ```solidity
   pool.addLiquidity(
       alice,   // owner — allowlisted, passes the check
       salt,
       deltas,
       callbackData,
       extensionData
   );
   ```
4. `_beforeAddLiquidity(bob, alice, ...)` is called → extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. Bob's callback pays the tokens; Alice receives the LP shares.
6. Bob has deposited into the pool despite not being on the allowlist. The `DepositAllowlistExtension` guard was bypassed.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
