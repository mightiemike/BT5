### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces the allowlist against the LP-position **owner** address rather than the **sender** (the actual caller who provides the tokens). Because `addLiquidity` lets the caller freely choose any `owner`, an address that is not on the allowlist can deposit into the pool by nominating an allowlisted address as `owner`, bypassing the guard entirely.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter and passes both `msg.sender` (the actual depositor) and `owner` (the LP-position recipient) to the extension hook: [1](#0-0) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` encodes both and forwards them to the extension: [2](#0-1) 

The extension receives `(sender, owner, ...)` but silently discards `sender` (first parameter is unnamed `address`) and checks only `owner`: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The admin-facing setter names its parameter `depositor`, confirming the intended semantics are about the depositing party, not the position recipient: [4](#0-3) 

Because `removeLiquidity` enforces `msg.sender == owner` before calling the hook, the mismatch only exists in the `addLiquidity` path. [5](#0-4) 

---

### Impact Explanation

Any address not on the allowlist can call `pool.addLiquidity(owner = <allowlisted_address>, ...)` and the guard passes. The unauthorized caller provides the tokens (via the liquidity callback); the allowlisted address receives the LP shares. The pool admin's access-control boundary — the entire purpose of `DepositAllowlistExtension` — is rendered ineffective. Pools that rely on this extension to enforce KYC, whitelist-only liquidity, or to prevent adversarial liquidity manipulation receive no protection.

---

### Likelihood Explanation

The bypass requires only a single call to `addLiquidity` with a known allowlisted address as `owner`. No special privileges, flash loans, or multi-step setup are needed. Any address that can interact with the pool can exploit this.

---

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of (or in addition to) `owner`:

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

1. Pool is deployed with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, Alice, true)`. Bob is **not** allowlisted.
3. Bob calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. Extension checks `allowedDepositor[pool][Alice]` → `true` → no revert.
6. Bob's tokens are pulled via the liquidity callback; Alice receives LP shares.
7. Bob has successfully deposited into a pool that was supposed to block him.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-207)
```text
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
