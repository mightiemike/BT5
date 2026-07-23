### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Share Recipient (`owner`) Instead of Token Payer (`sender`), Allowing Any Disallowed User to Bypass the Deposit Allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." However, its `beforeAddLiquidity` hook silently ignores the `sender` argument (the actual caller who pays tokens) and instead checks `owner` (the LP share recipient). Because `pool.addLiquidity` accepts an arbitrary `owner` address with no requirement that `msg.sender == owner`, any disallowed user can bypass the allowlist by supplying an allowed address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

There is no check anywhere in `addLiquidity` that `msg.sender == owner`. [2](#0-1) 

The extension hook receives `(sender, owner, ...)` but discards `sender` entirely (unnamed first parameter) and gates only on `owner`: [3](#0-2) 

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

`msg.sender` inside the extension is the pool (enforced by `onlyPool`), so the check resolves to `allowedDepositor[pool][owner]`. A disallowed user who knows any allowed address can call:

```solidity
pool.addLiquidity(allowedAddress, salt, deltas, callbackData, extensionData);
```

The extension sees `owner = allowedAddress`, finds it in the allowlist, and returns the success selector. The pool then mints LP shares to `allowedAddress` and calls back to `msg.sender` (the disallowed user) to pull tokens. The disallowed user's tokens enter the pool.

The same bypass works through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowedAddress, ...)` — `_validateOwner` only checks `owner != address(0)`, not that `owner == msg.sender`: [4](#0-3) 

---

### Impact Explanation

A disallowed user's tokens enter a curated pool without authorization. The pool admin's allowlist policy — intended to enforce KYC, compliance, or curation — is completely bypassed. The disallowed user's principal is now locked in the pool as liquidity, and the allowed `owner` receives LP shares they did not request. This constitutes broken core pool functionality (the allowlist guard fails open) and direct injection of unauthorized funds into the pool's reserve, which can affect LP claims and pool accounting.

---

### Likelihood Explanation

The attack requires no special privileges. Any unprivileged user can call `pool.addLiquidity` directly. Allowed addresses are publicly discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. The bypass is a single direct call with no preconditions beyond token approval. [5](#0-4) 

---

### Recommendation

Check `sender` (the actual token payer / caller) instead of `owner` (the LP share recipient). Rename the first parameter and gate on it:

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

When the pool is accessed through `MetricOmmPoolLiquidityAdder`, `sender` will be the adder's address. Pool admins using both the allowlist extension and the liquidity adder should explicitly allowlist the adder address, or the adder should forward the original `msg.sender` as an additional parameter so the extension can gate on the true end user.

---

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; allowedUser is allowlisted, attacker is not.
// allowedDepositor[pool][allowedUser] = true
// allowedDepositor[pool][attacker]    = false (not set)

// Attacker approves pool tokens and calls:
pool.addLiquidity(
    allowedUser,   // owner — passes the allowlist check
    salt,
    deltas,
    callbackData,
    extensionData
);
// Extension checks allowedDepositor[pool][allowedUser] == true → passes
// Pool mints LP shares to allowedUser
// Pool calls back to attacker (msg.sender) to pull tokens
// Attacker's tokens enter the pool — allowlist bypassed
```

The `removeLiquidity` path correctly enforces `msg.sender == owner`: [6](#0-5) 

The absence of an equivalent guard in `addLiquidity` is the root cause that makes the wrong-actor check in the extension exploitable. [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
