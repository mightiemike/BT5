### Title
`DepositAllowlistExtension.beforeAddLiquidity` Silently Discards the `sender` Argument and Checks `owner` Instead, Allowing Any Unlisted Address to Bypass the Deposit Gate - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook receives two address arguments: `sender` (the actual `msg.sender` of the pool call, i.e., the token payer) and `owner` (the position beneficiary). The implementation silently discards `sender` and enforces the allowlist only against `owner`. Any unlisted address can therefore bypass the gate by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the actual caller who will pay tokens through the callback; `owner` is the position beneficiary supplied by the caller.

`DepositAllowlistExtension.beforeAddLiquidity` is declared:

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

The first parameter — `sender`, the actual depositor — is unnamed and discarded. The guard evaluates `allowedDepositor[pool][owner]`. Because `owner` is a free caller-controlled argument to `addLiquidity`, any address can pass the check by setting `owner` to any address that the pool admin has allowlisted.

The contract's own NatSpec states *"Gates `addLiquidity` by depositor address, per pool"* and the storage mapping is named `allowedDepositor`, confirming the intended subject is the depositor, not the position beneficiary.

---

### Impact Explanation

A pool configured with `DepositAllowlistExtension` to restrict liquidity provision to a curated set of addresses (e.g., KYC-gated, institutional, or protocol-controlled depositors) provides **zero effective restriction**. Any unlisted address can:

1. Call `pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)`.
2. The extension checks `allowedDepositor[pool][allowlisted_address]` → `true`.
3. The deposit executes; the unlisted address pays tokens via the callback; the position is credited to `allowlisted_address`.

The pool admin's core invariant — only allowlisted addresses may add liquidity — is completely broken by an unprivileged caller. This is an admin-boundary break: a factory-configured access control is bypassed through a valid, non-privileged call path.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no unusual token behavior. Any EOA or contract can trigger it in a single transaction by choosing any already-allowlisted address as `owner`. The bypass is deterministic and unconditional whenever the extension is deployed and a pool is configured to use it.

---

### Recommendation

Replace the unnamed first parameter with `sender` and enforce the allowlist against it, consistent with the contract's stated purpose and the `SwapAllowlistExtension` pattern (which correctly checks `sender`):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
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

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT in the allowlist

Attack:
  bob calls pool.addLiquidity(alice, salt, deltas, callbackData, "")
    → pool calls _beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)
    → extension checks allowedDepositor[pool][alice] == true  ✓
    → deposit proceeds; bob pays tokens; position credited to alice

Result:
  bob (unlisted) successfully deposited into a restricted pool.
  The deposit allowlist is fully bypassed.
```

**Root cause location:** [1](#0-0) 

**Pool call site confirming `sender = msg.sender` (the actual token payer):** [2](#0-1) 

**`SwapAllowlistExtension` correctly checks `sender` (the actual caller) for comparison:** [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
