Looking at the `DepositAllowlistExtension`, I can identify a direct analog to H-8: a guard that checks the wrong entity, allowing the intended restriction to be bypassed entirely.

Let me read the key files to confirm the finding.

---

### Title
`DepositAllowlistExtension` checks LP recipient (`owner`) instead of actual depositor (`sender`), allowing any address to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**, but its `beforeAddLiquidity` hook silently discards the actual caller (`sender`) and checks only the LP-share recipient (`owner`). Any non-allowlisted address can bypass the deposit allowlist by specifying any allowlisted address as `owner`.

---

### Finding Description

The NatSpec and the mapping name both declare the intent:

> "Gates `addLiquidity` by depositor address, per pool."
> `mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;` [1](#0-0) 

But the `beforeAddLiquidity` hook discards the first `address` parameter (the actual caller) with a bare `address,` and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The first `address` parameter is the `sender` — the address that actually calls `addLiquidity` and provides the tokens. The `owner` is only the LP-share recipient. Because the hook checks `owner` instead of `sender`, any non-allowlisted address can call `addLiquidity` with any allowlisted address as `owner`, and the guard passes unconditionally.

The `allowedDepositor` mapping is public, so an attacker can trivially enumerate allowlisted addresses on-chain and use any of them as `owner`. [3](#0-2) 

---

### Impact Explanation

The deposit allowlist is completely neutralized. A pool configured as private (only allowlisted LPs) accepts liquidity from any address. Secondary effects:

- **Griefing of allowlisted owners**: The allowlisted `owner` receives LP shares they never requested. If the pool carries impermanent loss or is in a degraded state, those shares represent a loss of value forced on the victim.
- **Pool composition corruption**: The pool admin's intent to control who provides liquidity is broken; the pool's risk profile and liquidity composition can be manipulated by arbitrary actors.
- **Admin-boundary break**: An admin-configured access control (the allowlist) is bypassed by an entirely unprivileged path — matching the Metric OMM Allowed Impact Gate criterion.

---

### Likelihood Explanation

**High.** The bypass requires no special privilege, no flash loan, and no oracle manipulation. The attacker only needs to:
1. Read the public `allowedDepositor` mapping to find any allowlisted address.
2. Call `addLiquidity` with that address as `owner`.

The `allowedDepositor` mapping is public and emits `AllowedToDepositSet` events, making enumeration trivial. [4](#0-3) 

---

### Recommendation

Check the actual depositor (`sender`, the first parameter) instead of the LP recipient (`owner`):

```solidity
// BEFORE (broken):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// AFTER (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [5](#0-4) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached. Only `allowedAddress` is added to the allowlist via `setAllowedToDeposit`.
2. `nonAllowedAddress` (not in the allowlist) constructs `addLiquidity` parameters with `owner = allowedAddress`.
3. The pool calls `beforeAddLiquidity(nonAllowedAddress, allowedAddress, ...)` on the extension.
4. The hook evaluates `allowedDepositor[pool][allowedAddress]` → `true` → no revert.
5. `nonAllowedAddress` successfully deposits tokens into the pool; `allowedAddress` receives LP shares it never requested.
6. The deposit allowlist is fully bypassed. Repeating with different `nonAllowedAddress` callers demonstrates that the restriction is entirely ineffective.

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-13)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
