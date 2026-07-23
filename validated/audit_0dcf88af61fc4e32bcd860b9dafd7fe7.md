Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Controlled `owner` Instead of `sender`, Fully Bypassing the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and gates access only on `owner`, which is a freely caller-supplied argument to `MetricOmmPool.addLiquidity`. Any unprivileged address can bypass the deposit allowlist entirely by nominating any already-allowed address as `owner`, causing unauthorized liquidity to enter the pool and locking the attacker's tokens under the nominated owner's position key.

## Finding Description
`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from the caller and passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both and dispatches them to every registered extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` discards the first parameter (`sender`) by leaving it unnamed and gates only on `owner`: [3](#0-2) 

Because `owner` is freely chosen by the caller, any address can pass the check by nominating an already-allowed address as `owner`. The LP position is then recorded under the nominated `owner`, and the attacker cannot withdraw it because `removeLiquidity` enforces `msg.sender == owner`: [4](#0-3) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller): [5](#0-4) 

The asymmetry between the two sibling extensions confirms the deposit extension is checking the wrong address.

## Impact Explanation
The deposit allowlist is the pool admin's mechanism to enforce access control on liquidity provision (e.g., KYC-gated or institutional-only pools). The bypass makes the allowlist completely ineffective: any unprivileged address can deposit into a restricted pool by setting `owner` to any address that appears in `allowedDepositor[pool]`. Unauthorized funds enter the pool, violating the core invariant that only permitted depositors can add liquidity. Additionally, the nominated `owner` receives an unsolicited LP position it never requested, constituting an unsolicited change to its on-chain state and pool exposure. The attacker's deposited tokens are irrevocably locked under the nominated owner's position key, making this also a griefing vector. This constitutes broken core pool functionality causing unauthorized fund entry and admin-boundary bypass by an unprivileged path.

## Likelihood Explanation
The bypass requires no special permissions, no flash loans, no oracle manipulation, and no multi-step setup. Any EOA or contract can call `addLiquidity` with `owner` set to any address already on the allowlist. The allowlist is trivially enumerable from on-chain `AllowedToDepositSet` events. The attack is executable in a single transaction and is repeatable indefinitely.

## Recommendation
Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, mirroring the pattern used by `SwapAllowlistExtension`:

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

Also update `setAllowedToDeposit`, `allowedDepositor`, and `isAllowedToDeposit` to reflect that they key on the depositing sender, not the position owner, to avoid future confusion.

## Proof of Concept
**Setup:**
- Pool `P` has `DepositAllowlistExtension` configured.
- Pool admin calls `setAllowedToDeposit(P, Alice, true)`.
- Bob is **not** on the allowlist.

**Attack:**
1. Bob calls `P.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData="")`.
2. Pool calls `_beforeAddLiquidity(msg.sender=Bob, owner=Alice, ...)`.
3. Extension evaluates `allowedDepositor[P][Alice]` → `true` → no revert.
4. `LiquidityLib.addLiquidity` executes: Bob's tokens are pulled via the swap callback, and the LP position is recorded under `(Alice, salt=0)`.
5. Bob cannot withdraw (enforced by `if (msg.sender != owner) revert NotPositionOwner()` in `removeLiquidity`).
6. Alice holds an LP position she never requested; the pool has accepted unauthorized liquidity.

**Foundry test sketch:**
```solidity
// Bob (not allowlisted) calls addLiquidity with owner=Alice (allowlisted)
vm.prank(bob);
pool.addLiquidity(alice, 0, deltas, callbackData, "");
// Assert: position exists under alice, bob's tokens transferred, no revert occurred
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
