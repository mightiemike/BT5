### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Unauthorized Deposits — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces the deposit allowlist against the `owner` parameter (the LP position recipient) rather than the `sender` parameter (the actual caller who provides tokens via callback). Any address not on the allowlist can bypass the guard by specifying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts two distinct address arguments:

- `msg.sender` → forwarded as `sender` to the extension hook — this is the address that **provides tokens** via the swap callback.
- `owner` → caller-supplied — this is the address that **receives the LP position**. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` passes both to the hook: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first parameter is unnamed) and gates only on `owner`:

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

The admin-facing setter names the parameter `depositor`, confirming the intended subject of the guard is the caller, not the position recipient: [4](#0-3) 

Because `owner` is freely chosen by the caller, any unauthorized address can pass the check by supplying an allowlisted address as `owner`. The tokens are still pulled from the unauthorized caller via the callback; the allowlisted address receives the LP position it never requested.

---

### Impact Explanation

The deposit allowlist guard is fully bypassed. An unauthorized address can deposit into a pool that is administratively restricted to specific depositors. This breaks the core access-control invariant the extension is designed to enforce. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, or whitelist-only liquidity), this allows:

- Unauthorized parties to inject liquidity into a restricted pool, diluting or distorting the LP composition.
- Griefing of allowlisted addresses by forcing unwanted LP positions onto them (since `removeLiquidity` requires `msg.sender == owner`, the allowlisted address must act to unwind the position). [5](#0-4) 

---

### Likelihood Explanation

The bypass requires only a single `addLiquidity` call with `owner` set to any allowlisted address. No privileged access, flash loan, or multi-step setup is needed. Any address that can call the pool (or a router that calls the pool) can trigger this. Likelihood is **High**.

---

### Recommendation

Replace the `owner` check with a `sender` check, matching the intent of `setAllowedToDeposit`:

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

This ensures the guard applies to the address that actually provides tokens, not the address that receives the position.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)`. Only `alice` is allowlisted.
3. `bob` (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. The pool calls `extension.beforeAddLiquidity(bob, alice, ...)`.
5. The check evaluates `allowedDepositor[pool][alice]` → `true` → **no revert**.
6. `bob`'s callback is invoked; `bob` transfers tokens into the pool.
7. `alice` receives the LP position. `bob` has bypassed the allowlist entirely.
8. `alice` must now call `removeLiquidity` herself to unwind the unwanted position. [3](#0-2) [6](#0-5)

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
