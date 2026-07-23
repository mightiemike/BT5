### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Unauthorized Actor to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and gates on `owner` (the position beneficiary) instead. Because `MetricOmmPool.addLiquidity` lets any caller supply an arbitrary `owner`, any actor not in the allowlist can bypass the guard by naming an authorized address as `owner`, deposit tokens into the pool via the callback, and have the LP shares credited to that address.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling` forwards both arguments faithfully:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension`, the first positional argument (`sender`) is unnamed and discarded; the guard reads only `owner`:

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

Because `addLiquidity` imposes no restriction on who may supply the `owner` parameter, any caller can pass an allowlisted address as `owner`. The hook then approves the call, the unauthorized caller's callback pays the tokens, and the LP shares are minted to the named `owner`.

The sister extension `SwapAllowlistExtension` demonstrates the correct pattern — it checks `sender` (the actual caller), not the `recipient`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [4](#0-3) 

The inconsistency confirms the check in `DepositAllowlistExtension` is erroneous.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may inject tokens into the pool (e.g., KYC/AML compliance, curated LP sets, regulatory perimeters). With the guard checking `owner` instead of `sender`:

- Any unprivileged actor can deposit tokens into an allowlist-gated pool, fully circumventing the admin-configured access control.
- The unauthorized depositor pays real tokens via the `metricOmmAddLiquidityCallback`; those tokens enter the pool's bin accounting and are credited as LP shares to the named `owner`.
- The named `owner` receives an unsolicited position; if they are a contract without `removeLiquidity` logic, the tokens are effectively stranded.
- An attacker can also use this to inflate bin balances before a swap, shifting the per-share metrics read by `OracleValueStopLossExtension.afterSwap` and potentially suppressing a stop-loss trigger that would otherwise protect LPs.

This is a direct admin-boundary break: an unprivileged path bypasses a factory-initialized, pool-admin-controlled guard.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with any allowlisted address as `owner`. No special permissions, flash loans, or oracle manipulation are needed. Any on-chain actor can execute this in a single transaction.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

If the intent is to gate on the position owner (not the payer), the contract name, NatSpec, and `setAllowedToDeposit` function name must be updated to reflect that, and the security model must be re-evaluated.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  allowedDepositor[pool][bob]   = false   // bob is NOT allowed

Attack (single tx, no special role):
  bob calls pool.addLiquidity(
      owner

### Citations

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
