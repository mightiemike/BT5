### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` parameter (the address that will receive LP shares) rather than the actual caller (`sender`, i.e., `msg.sender` of the pool call). Because `addLiquidity` accepts an arbitrary `owner` address with no restriction on who may supply it, any address — including one not on the allowlist — can bypass the guard by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the `beforeAddLiquidity` hook with `msg.sender` as the first argument and the caller-supplied `owner` as the second: [1](#0-0) 

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first argument (the real caller) and checks only `owner`: [2](#0-1) 

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

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks the first argument (`sender`): [3](#0-2) 

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

The asymmetry is the root cause: `beforeSwap` guards the actual caller; `beforeAddLiquidity` guards only the beneficiary of the LP shares.

Because `addLiquidity` imposes no restriction on who may pass an arbitrary `owner` value (unlike `removeLiquidity`, which enforces `msg.sender == owner`): [4](#0-3) 

…any address can call `addLiquidity(allowlisted_address, salt, deltas, ...)`, pass the allowlist check (because `owner` is allowlisted), supply tokens through the swap callback, and have those tokens credited to the pool — all while the actual depositor is never verified.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting which addresses may inject liquidity. With this bug:

1. **Allowlist fully bypassed**: Any address, regardless of allowlist status, can deposit tokens into a restricted pool by naming any allowlisted address as `owner`.
2. **Forced LP positions**: The allowlisted `owner` receives LP shares they never requested, which can be used to grief them (e.g., force unwanted exposure, complicate their accounting, or lock them into a position they must actively unwind).
3. **Pool receives tokens from unauthorized sources**: The pool's intended access control — e.g., KYC/AML gating, curated market-maker whitelists, or regulatory compliance — is rendered ineffective.

This fits the "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" category in the allowed impact gate.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a known allowlisted address as `owner`. No special privileges, flash loans, or oracle manipulation are needed. Any address that can observe the allowlist state (public mappings) can exploit this immediately.

---

### Recommendation

Change `beforeAddLiquidity` to check the first parameter (the actual caller/sender) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Also update `setAllowedToDeposit`, `isAllowedToDeposit`, and the `allowedDepositor` mapping semantics to reflect that the allowlisted entity is the caller, not the LP share recipient.

---

### Proof of Concept

1. Pool `P` is deployed with `DepositAllowlistExtension` configured. `allowAllDepositors[P] = false`.
2. Admin calls `setAllowedToDeposit(P, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) calls `P.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. `beforeAddLiquidity` is invoked with `sender = bob` (ignored) and `owner = alice`.
5. The check `allowedDepositor[P][alice]` returns `true` → no revert.
6. Bob's callback transfers tokens into the pool; Alice receives LP shares.
7. Bob has successfully deposited into a restricted pool without being on the allowlist.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
