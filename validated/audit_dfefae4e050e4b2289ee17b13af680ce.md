Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual depositor, i.e. `msg.sender` of the pool call) and gates on `owner` (the LP position recipient) instead. Any unprivileged caller can bypass the allowlist by supplying any already-allowlisted address as `owner`, depositing tokens into a gated pool without being on the allowlist themselves. This breaks the admin-configured access-control invariant that the extension is designed to enforce.

## Finding Description
The pool's `addLiquidity` function passes `msg.sender` as `sender` and the caller-supplied `owner` as the position recipient:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The extension interface defines these as two distinct parameters:

```solidity
// IMetricOmmExtensions.sol lines 14-20
function beforeAddLiquidity(address sender, address owner, ...) external returns (bytes4);
```

However, `DepositAllowlistExtension.beforeAddLiquidity` unnamed-discards `sender` and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-38
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The contract name, the mapping name (`allowedDepositor`), and the NatSpec ("Gates `addLiquidity` by depositor address") all declare the intent is to gate the **depositor** (`sender`). The implementation gates the **position recipient** (`owner`) instead.

**Direct pool bypass:**
1. Attacker (not allowlisted) calls `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)`.
2. Pool fires `_beforeAddLiquidity(msg.sender=attacker, owner=allowlistedAddress, ...)`.
3. Extension checks `allowedDepositor[pool][allowlistedAddress]` → `true` → passes.
4. Attacker's tokens enter the pool; LP shares are minted to `allowlistedAddress`.

**`MetricOmmPoolLiquidityAdder` bypass:**
`addLiquidityExactShares(pool, owner, ...)` (lines 56-68) allows any caller to supply an arbitrary `owner`. It calls `pool.addLiquidity(positionOwner, ...)` where `msg.sender` of the pool call is the `LiquidityAdder` contract. The extension receives `sender = LiquidityAdder`, `owner = user-supplied`. Since the check is on `owner`, any caller who supplies an allowlisted `owner` address passes the gate regardless of who they are.

No existing guard compensates for this: `_validateOwner` only checks for `address(0)`, and the reentrancy guard is unrelated to allowlist enforcement.

## Impact Explanation
The deposit allowlist is an admin-configured guard intended to restrict which addresses may deposit into a pool (e.g., for KYC/compliance or controlled liquidity bootstrapping). Because the check is on `owner` rather than `sender`, the guard is fully bypassed: any unprivileged address can deposit tokens into a gated pool by nominating any already-allowlisted address as `owner`. The pool receives tokens from non-allowlisted depositors, violating the admin's access-control invariant. This is an admin-boundary break: an admin-configured guard is bypassed by an unprivileged path.

## Likelihood Explanation
The bypass requires only a standard `pool.addLiquidity` call with `owner` set to any known allowlisted address. No special privileges, flash loans, or oracle manipulation are needed. Any on-chain observer can read the allowlist state (via `isAllowedToDeposit` or `allowedDepositor`) and construct the bypass call. Likelihood is high whenever a pool deploys this extension with a non-empty allowlist.

## Recommendation
Replace the unnamed first parameter with `sender` and gate on it instead of `owner`:

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

Pool admins using this extension via `MetricOmmPoolLiquidityAdder` must also allowlist the `LiquidityAdder` contract address as a `sender` (or use `setAllowAllDepositors`) and rely on the adder's own `msg.sender` context for user-level gating, or extend the extension to check both `sender` and `owner`.

## Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only `alice` is allowlisted as depositor.
// allowedDepositor[pool][alice] = true
// allowedDepositor[pool][attacker] = false

// Attacker bypasses the allowlist:
vm.prank(attacker);
pool.addLiquidity(
    alice,          // owner = allowlisted address → check passes
    salt,
    deltas,
    callbackData,
    extensionData
);
// Result: attacker's tokens enter the pool; alice receives LP shares.
// The deposit allowlist did not block the non-allowlisted attacker.
```