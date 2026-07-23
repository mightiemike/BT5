Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Recipient (`owner`) Instead of Actual Depositor (`sender`), Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead checks the user-supplied `owner` address (the LP position recipient) against the allowlist. Any address not on the allowlist can bypass the restriction by calling `addLiquidity` with `owner` set to any allowlisted address, rendering the deposit allowlist completely ineffective.

## Finding Description
In `MetricOmmPool.addLiquidity`, the pool calls the extension hook with `sender = msg.sender` (the actual depositor) and `owner` = the user-supplied LP recipient: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` names the first parameter with a blank (`address,`), discarding `sender` entirely, and performs the allowlist check only on `owner`: [2](#0-1) 

Because `msg.sender` inside the extension is the pool (enforced by `onlyPool`), the check `allowedDepositor[msg.sender][owner]` resolves to `allowedDepositor[pool][owner]` — it asks whether the **LP recipient** is allowlisted, not whether the **depositor** is allowlisted. This is confirmed by comparing with `SwapAllowlistExtension.beforeSwap`, which correctly names and checks `sender`: [3](#0-2) 

The allowlist mapping `allowedDepositor` is keyed by `(pool, depositor)` per the admin setter: [4](#0-3) 

But the hook reads `allowedDepositor[msg.sender][owner]` where `owner` is the attacker-controlled LP recipient, not the actual depositor. The wrong value checked is the extension decision (`allowedDepositor[pool][owner]` instead of `allowedDepositor[pool][sender]`).

## Impact Explanation
The deposit allowlist — the pool admin's primary access-control mechanism for liquidity — is rendered completely ineffective. Any unprivileged address can deposit into a restricted pool by setting `owner` to any allowlisted address. The attacker modifies pool bin state (token balances, share totals, `binTotals`, `_binTotalShares`, `_positionBinShares`) without authorization. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged caller in a single transaction.

## Likelihood Explanation
Exploitation requires only knowing one allowlisted address, which is publicly readable on-chain via `allowedDepositor(pool, address)`. No special privileges, flash loans, or complex setup are needed. Any EOA or contract can execute the bypass in a single transaction by supplying any allowlisted address as `owner`. The condition is trivially satisfiable on any pool using `DepositAllowlistExtension` with at least one allowlisted depositor.

## Recommendation
Name and check `sender` (the actual depositor) instead of `owner` (the LP recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
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

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension` as an extension.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. Attacker (`bob`, not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner = alice,   // allowlisted address
       salt  = 0,
       deltas = <any valid delta>,
       callbackData = ...,
       extensionData = ""
   );
   ```
4. Pool calls `_beforeAddLiquidity(msg.sender=bob, owner=alice, ...)` which encodes and dispatches to `extension.beforeAddLiquidity(sender=bob, owner=alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `bob`'s tokens are pulled from `bob` via the liquidity callback; LP shares are credited to `alice`.
7. `bob` has successfully deposited into a restricted pool, modifying `binTotals`, `_binTotalShares`, and `_positionBinShares` without being on the allowlist.
8. `bob` can repeat with different bin configurations to manipulate the pool's liquidity distribution at will.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
