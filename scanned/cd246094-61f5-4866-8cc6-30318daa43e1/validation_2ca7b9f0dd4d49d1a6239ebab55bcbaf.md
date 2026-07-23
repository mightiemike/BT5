### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor** address. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller of `pool.addLiquidity`) and instead checks `owner` — a free parameter the caller supplies. Any unprivileged actor can bypass the allowlist by naming any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension: [1](#0-0) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional argument (`sender`) is unnamed and discarded. The guard is evaluated against `owner`: [2](#0-1) 

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

Because `owner` is a free parameter any caller can set to any address, an actor **not** on the allowlist can call `pool.addLiquidity(owner = Alice, ...)` where Alice is allowlisted. The extension evaluates `allowedDepositor[pool][Alice] == true` and does not revert. The unauthorized caller pays the tokens; Alice receives the LP shares.

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller): [3](#0-2) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The structural inconsistency — `sender` checked in the swap guard, `owner` checked in the deposit guard — confirms the deposit guard is checking the wrong identity.

The `setAllowedToDeposit` admin setter and `isAllowedToDeposit` view both use the name `depositor`, reinforcing that the intended subject is the caller, not the position owner: [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is rendered completely ineffective. Any unprivileged caller who knows a single allowlisted address can deposit into the gated pool by supplying that address as `owner`. The pool admin's intent to restrict who provides liquidity is bypassed without any special privilege, flash loan, or complex setup. This is an admin-boundary break: a pool-level role check is bypassed by an unprivileged path, matching the allowed impact gate.

---

### Likelihood Explanation

Allowlisted addresses are observable on-chain (emitted in `AllowedToDepositSet` events). The bypass requires a single `addLiquidity` call with a known allowlisted address as `owner`. No elevated privilege, multi-step setup, or waiting period is needed. Any actor can exploit this at any time after the pool is deployed.

---

### Recommendation

Check `sender` (the actual caller) instead of `owner`:

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

---

### Proof of Concept

1. Pool `P` is deployed with `DepositAllowlistExtension` as an extension.
2. Pool admin calls `setAllowedToDeposit(P, Alice, true)`. Only Alice is permitted to deposit.
3. Bob (not on the allowlist) calls `P.addLiquidity(owner = Alice, salt = 0, deltas = ..., ...)`.
4. Pool invokes `_beforeAddLiquidity(sender = Bob, owner = Alice, ...)`.
5. Extension evaluates `allowedDepositor[P][Alice] == true` → no revert.
6. Bob's tokens are transferred into the pool; Alice receives LP shares.
7. Bob has deposited into the allowlist-gated pool without being on the allowlist, fully bypassing the access control the pool admin configured.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-29)
```text
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
