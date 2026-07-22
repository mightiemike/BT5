### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Un-Allowlisted Addresses to Bypass the Deposit Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook validates the `owner` (LP position beneficiary) against the per-pool allowlist, but silently ignores the `sender` (the address actually calling `addLiquidity` and supplying tokens). Any un-allowlisted address can bypass the guard by nominating an allowlisted address as the LP position `owner`.

---

### Finding Description

`ExtensionCalling._beforeAddLiquidity` forwards two distinct addresses to every registered extension: [1](#0-0) 

- `sender` — the address that called `pool.addLiquidity()` and is providing tokens.
- `owner` — the address that will own the resulting LP position (tracked by owner/salt).

`DepositAllowlistExtension.beforeAddLiquidity` receives both but discards `sender` (unnamed first parameter) and only checks `owner`: [2](#0-1) 

The check on line 38 is:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
```

`msg.sender` here is the pool (correct), but the second key is `owner`, not `sender`. The actual depositor (`sender`) is never validated.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender`: [3](#0-2) 

The asymmetry confirms the deposit extension is checking the wrong address.

Additionally, the override drops the `onlyPool` modifier that the base class applies to `beforeAddLiquidity`: [4](#0-3) 

The override is declared `view` and carries no `onlyPool` guard, meaning the function can be called by any address — though this secondary issue is less impactful because `msg.sender` is used as the pool key and no allowlist would be set for an arbitrary caller.

---

### Impact Explanation

A pool configured with `DepositAllowlistExtension` is intended to be a permissioned liquidity venue (e.g., KYC-gated, institutional-only). The guard is supposed to ensure only approved addresses can supply liquidity. Because `sender` is never checked:

1. **Allowlist bypass**: Any un-allowlisted address `B` can call `pool.addLiquidity(owner = A, ...)` where `A` is any allowlisted address. The extension sees `allowedDepositor[pool][A] == true` and passes. `B` successfully injects liquidity into a permissioned pool without approval.
2. **Liquidity manipulation**: `B` can place tokens into specific bins, altering the pool's depth ladder and affecting oracle-anchored pricing for subsequent swaps — a fund-impacting consequence for existing LPs.
3. **Collusion path**: `B` and `A` collude: `B` deposits with `owner = A`, `A` later removes liquidity and returns tokens to `B` off-chain. This gives `B` full economic participation in the pool with zero allowlist enforcement.

This is an **admin-boundary break**: the pool admin's configured access control is bypassed by an unprivileged path.

---

### Likelihood Explanation

- **Low effort**: The bypass requires only knowing one allowlisted address (trivially discoverable on-chain via `AllowedToDepositSet` events) and calling `addLiquidity` with that address as `owner`.
- **No special role needed**: Any EOA or contract can execute this.
- **Always applicable**: Any pool that has at least one allowlisted depositor (i.e., any pool where the extension is meaningfully configured) is vulnerable.

Likelihood: **Medium-High**.

---

### Recommendation

Change the `beforeAddLiquidity` check to validate `sender` (the actual depositor) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// current (wrong):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// fixed:
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

If the intent is to restrict both the caller and the position owner, check both. Also restore the `onlyPool` modifier on the override.

---

### Proof of Concept

```
Setup:
  pool P has DepositAllowlistExtension E configured
  pool admin sets: allowedDepositor[P][Alice] = true
  Bob is NOT on the allowlist

Attack:
  Bob calls pool.addLiquidity(
      owner  = Alice,   // allowlisted address
      salt   = 0,
      deltas = <Bob's tokens>,
      ...
  )

Extension check (line 38 of DepositAllowlistExtension.sol):
  msg.sender                    = P      (pool, correct)
  allowAllDepositors[P]         = false
  allowedDepositor[P][Alice]    = true   ← owner is checked, not sender
  → check passes, no revert

Result:
  Bob's tokens enter pool P.
  Alice holds the LP position (owner = Alice, salt = 0).
  Bob has bypassed the deposit allowlist entirely.
  Bob and Alice collude: Alice removes liquidity, returns tokens to Bob off-chain.
  Bob has effectively participated in a permissioned pool without approval.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L45-52)
```text
  function beforeAddLiquidity(address, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
