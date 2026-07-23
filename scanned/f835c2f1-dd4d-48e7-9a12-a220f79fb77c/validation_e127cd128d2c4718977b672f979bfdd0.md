### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unpermissioned caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the position `owner` parameter rather than the `sender` (the actual caller who provides tokens via callback). Because `addLiquidity` accepts an arbitrary `owner` address, any unpermissioned caller can bypass the deposit allowlist by naming an already-allowed address as `owner`, while the caller's own address is never checked.

### Finding Description

`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both the actual caller (`sender = msg.sender`) and the caller-supplied position owner (`owner`) to every configured extension. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both values and dispatches them to extensions in the configured order: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first parameter (`sender`) and checks only `owner`:

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

The admin-facing setter names its parameter `depositor`, confirming the intent is to gate the token-providing caller, not the position owner: [4](#0-3) 

Because `LiquidityLib.addLiquidity` issues the token-transfer callback against `msg.sender` (the actual caller), not `owner`, the token provider and the position owner are two distinct addresses. The guard checks the wrong one. [5](#0-4) 

### Impact Explanation

Any address not on the allowlist can call `addLiquidity(owner = <allowedAddress>, ...)`, pass the extension check, and inject tokens into the pool. The deposit allowlist — the sole mechanism the pool admin has to restrict who provides liquidity — is rendered completely ineffective. This is an admin-boundary break: a pool admin-configured guard is bypassed by an unprivileged path, breaking the core restricted-deposit functionality of the extension.

### Likelihood Explanation

No special conditions are required. The attacker only needs to know one address that the pool admin has already allowlisted (e.g., any existing LP). The call is a standard `addLiquidity` invocation with a different `owner` argument. No privileged access, no oracle manipulation, and no collusion with the protocol is needed to trigger the bypass.

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the first parameter) instead of `owner`:

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

This aligns the check with the entity that actually transfers tokens into the pool.

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
3. Bob calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
4. `_beforeAddLiquidity(msg.sender=Bob, owner=alice, ...)` is dispatched to the extension.
5. Extension evaluates `allowedDepositor[pool][alice] == true` → no revert.
6. `LiquidityLib.addLiquidity` credits shares to `alice`'s position; the token-transfer callback fires against Bob, pulling Bob's tokens into the pool.
7. Bob has successfully deposited into a pool that was supposed to reject him. The allowlist check never touched Bob's address.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L192-195)
```text
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
