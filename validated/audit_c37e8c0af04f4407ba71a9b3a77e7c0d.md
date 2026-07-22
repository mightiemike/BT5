### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**, but its `beforeAddLiquidity` hook silently drops the `sender` argument and checks `owner` instead. Because `owner` is a free caller-controlled parameter in `MetricOmmPool.addLiquidity`, any address—regardless of allowlist status—can deposit into a restricted pool by naming any allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments: `sender` (the actual `msg.sender` of the pool call, who pays tokens via callback) and `owner` (the LP-position beneficiary, a free parameter). The implementation silently discards `sender` and gates only on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool dispatches the hook with `sender = msg.sender` and `owner` as the caller-supplied parameter:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both arguments to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [3](#0-2) 

Because `owner` is a free parameter chosen by the caller, any non-allowlisted address can pass the guard by supplying any allowlisted address as `owner`. The non-allowlisted caller pays the tokens (via the swap callback), while the allowlisted `owner` receives the LP shares — the guard never inspects who is actually depositing.

The contract's own NatSpec confirms the intended semantics are the opposite of what is implemented:

```solidity
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
``` [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is completely nullified. Any non-allowlisted address can add liquidity to a restricted pool by specifying any allowlisted address as `owner`. Concrete consequences:

1. **Admin-boundary break**: The pool admin's allowlist configuration—the only mechanism to restrict depositors—is bypassed by an unprivileged path with no special conditions.
2. **Forced LP position on allowlisted address**: The allowlisted `owner` receives LP shares they never requested. If the pool subsequently suffers impermanent loss or is drained, the allowlisted address bears that loss.
3. **Compliance/security failure**: If the allowlist enforces KYC, AML, or counterparty restrictions, those controls are rendered inoperative.

---

### Likelihood Explanation

Exploitation requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity(owner = <any allowlisted address>, ...)` and the guard passes unconditionally. The bypass is always available as long as at least one address is on the allowlist (which is the normal operating state of the extension).

---

### Recommendation

Check `sender` (the actual depositor paying tokens) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and sets `allowedDepositor[pool][alice] = true`. No other address is allowlisted.
2. Non-allowlisted address `attacker` calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
3. The pool calls `_beforeAddLiquidity(msg.sender=attacker, owner=alice, ...)`.
4. The extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `attacker` pays tokens via the liquidity callback; `alice` receives LP shares.
6. The deposit allowlist is fully bypassed: `attacker` deposited into a pool it was explicitly excluded from, and `alice` now holds an LP position she never initiated.

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-12)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```
