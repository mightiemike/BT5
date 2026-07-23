### Title
`DepositAllowlistExtension` gates `owner` (position recipient) instead of `sender` (actual depositor/payer), allowing any unpermissioned caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual caller of `addLiquidity`, who pays the tokens) and instead checks `owner` (the position recipient, a freely caller-controlled parameter). Because `owner` is supplied by the caller, any unpermissioned address can bypass the allowlist gate by setting `owner` to any address that is already on the allowlist.

---

### Finding Description

`MetricOmmPool.addLiquidity` dispatches the before-hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (caller-supplied)
``` [1](#0-0) 

The hook signature receives both identities, but the implementation silently drops `sender` (first argument unnamed) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

`owner` is a parameter the caller supplies freely. An unpermissioned address `Bob` can call:

```
pool.addLiquidity(alice /*allowlisted*/, salt, deltas, callbackData, extensionData)
```

The extension evaluates `allowedDepositor[pool][alice]` → `true` and passes. The pool then calls `metricOmmModifyLiquidityCallback` on `Bob` (`msg.sender`), so **Bob pays the tokens** while **Alice receives the LP position**. The allowlist gate is completely bypassed.

The `_validateOwner` helper in `MetricOmmPoolLiquidityAdder` only rejects `address(0)` and does not enforce `owner == msg.sender`, so the same bypass works through the adder's `addLiquidityExactShares(pool, owner, ...)` overload. [3](#0-2) 

The project's own audit-target notes confirm the intended invariant: *"the checked identity has to be exactly the one the pool intends to gate"* and *"assert the allowlist always gates the economically relevant depositor."* [4](#0-3) 

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism pool admins have to restrict who may provide liquidity. Checking `owner` instead of `sender` makes the gate trivially bypassable: any address can deposit into a restricted pool by naming an allowlisted address as `owner`. This breaks the admin-boundary invariant (an unprivileged path bypasses an admin-configured access control), allows unauthorized liquidity provision into pools designed for controlled LP sets, and can be used to grief allowlisted LPs by forcing unwanted positions onto their addresses.

---

### Likelihood Explanation

The bypass requires only knowing one allowlisted address (readable from public `allowedDepositor` mapping) and calling `addLiquidity` directly on the pool. No special privileges, flash loans, or oracle manipulation are needed. Every pool that deploys `DepositAllowlistExtension` with a non-empty allowlist is affected on every `addLiquidity` call. [5](#0-4) 

---

### Recommendation

Replace the ignored first argument with a named `sender` and gate on it instead of `owner`:

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

`sender` is `msg.sender` of the `addLiquidity` call — the entity that will pay tokens via the callback — which is the economically relevant identity the allowlist is meant to gate.

---

### Proof of Concept

1. Deploy a pool with `DepositAllowlistExtension` configured as the `beforeAddLiquidity` hook.
2. Call `extension.setAllowedToDeposit(pool, alice, true)` from the pool admin. Bob is **not** allowlisted.
3. From Bob's address, call `pool.addLiquidity(alice, 0, deltas, callbackData, "")` directly.
4. Observe: the extension evaluates `allowedDepositor[pool][alice]` → `true` and does **not** revert.
5. The pool calls `metricOmmModifyLiquidityCallback` on Bob; Bob's tokens are pulled and Alice receives the LP shares.
6. Bob has successfully deposited into a pool he was not permitted to access. [2](#0-1) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-14)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** generate_scanned_questions.py (L647-654)
```python
            short="deposit allowlist gate",
            file_function="metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*",
            call_path="public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner",
            values="the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares",
            control_hint="The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate.",
            validation_focus="Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.",
        ),
```
