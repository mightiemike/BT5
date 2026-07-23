Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension` is intended to gate `addLiquidity` by the depositing caller's address. However, `beforeAddLiquidity` silently discards the `sender` parameter and instead validates `owner` (the LP-position recipient). Because `sender` and `owner` are distinct arguments forwarded separately by the core, any address not on the allowlist can bypass the gate by specifying an allowlisted address as `owner`.

## Finding Description

`setAllowedToDeposit` stores permissions keyed by `depositor`: [1](#0-0) 

`beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed (discarded). The guard then reads `allowedDepositor[msg.sender][owner]`, where `owner` is the LP-position recipient, not the actual caller: [2](#0-1) 

The core's `_beforeAddLiquidity` in `ExtensionCalling` forwards both `sender` and `owner` as distinct, independent arguments: [3](#0-2) 

Because the hook ignores `sender` entirely, the allowlist check is applied to the wrong principal. An attacker calls `addLiquidity` with `owner = trustedLP` (any allowlisted address). The pool calls `extension.beforeAddLiquidity(attacker, trustedLP, ...)`. The hook checks `allowedDepositor[pool][trustedLP]` → `true` → no revert. The attacker successfully adds liquidity despite never being allowlisted.

## Impact Explanation

The deposit-gating invariant enforced by `DepositAllowlistExtension` is completely nullified. Any unprivileged address can add liquidity to a pool restricted for compliance, KYC gating, or LP-set restriction by simply naming an allowlisted address as `owner`. This is broken core pool functionality causing unauthorized access to restricted pools, matching the allowed impact of broken core pool functionality.

## Likelihood Explanation

Exploitation requires only that `sender != owner`, which is the standard case whenever a router, aggregator, or multicall contract calls `addLiquidity` on behalf of a user — the dominant interaction pattern in DeFi. No special privileges, flash loans, or exotic tokens are needed. Any address aware of the mismatch can exploit it immediately and repeatably.

## Recommendation

Replace the `owner` check with `sender` to match the semantic intent of `allowedDepositor`:

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

If the intent is to restrict LP-position ownership rather than the caller, rename the mapping and setter to `allowedOwner`/`setAllowedToOwn` to make the design explicit.

## Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, trustedLP, true)`.
2. `attacker` (not allowlisted) calls `pool.addLiquidity(deltas, extensionData)` with `owner = trustedLP`.
3. The pool calls `extension.beforeAddLiquidity(attacker, trustedLP, ...)` via `_beforeAddLiquidity`.
4. The hook evaluates `allowedDepositor[pool][trustedLP]` → `true` → no revert.
5. `attacker` successfully adds liquidity to the restricted pool despite never being allowlisted.

### Citations

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
