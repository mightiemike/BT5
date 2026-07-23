Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks LP-recipient `owner` instead of depositor `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and validates only `owner` — the LP-position recipient, which is a free caller-controlled argument. Any address not on the allowlist can bypass the guard by supplying any already-allowlisted address as `owner`, completely defeating the access-control mechanism for restricted pools.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as the depositor and `owner` as the LP-position recipient to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` correctly forwards both identities to the extension:

```solidity
// ExtensionCalling.sol lines 95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

However, `DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter (`sender`) as unnamed and ignored, then checks only `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a parameter chosen freely by the caller of `addLiquidity`. An attacker sets `owner` to any address the pool admin has already allowlisted. The check `allowedDepositor[pool][trustedLP]` returns `true`, the hook passes, and the attacker's swap callback delivers tokens — all without the attacker's address ever being validated.

**Exploit path:**
1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `trustedLP`.
2. Attacker (not on the allowlist) calls `pool.addLiquidity(owner = trustedLP, ...)`.
3. Pool calls `extension.beforeAddLiquidity(sender=attacker, owner=trustedLP, ...)`.
4. Extension checks `allowedDepositor[pool][trustedLP]` → `true` → no revert.
5. Attacker's `metricOmmSwapCallback` delivers tokens; LP shares are minted to `trustedLP`.
6. Attacker has added liquidity to a restricted pool without authorization.

Existing guards are insufficient: the reentrancy guard and length checks in `addLiquidity` do not validate caller identity, and the extension hook is the sole access-control mechanism for deposit allowlisting.

## Impact Explanation
The deposit allowlist is the pool admin's primary mechanism for restricting who can supply liquidity (e.g., KYC-gated or institutional-only pools). Because the guard checks the wrong identity, it is entirely ineffective. Any address can deposit into a restricted pool, altering bin balances (`binTotals.scaledToken0`/`scaledToken1`, `_binStates`, `_binTotalShares`, `_positionBinShares`), influencing oracle-anchored pricing, and modifying pool state in pools that were explicitly restricted. This constitutes a broken core pool functionality and an admin-boundary break allowing unauthorized parties to affect pool state and LP share accounting.

## Likelihood Explanation
Exploitation requires no special permissions, no flash loan, and no complex setup. Any EOA or contract can call `addLiquidity` with an arbitrary `owner`. Allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. The attack is repeatable at will with zero cost beyond gas.

## Recommendation
Replace the unnamed first parameter with `sender` and check it instead of `owner`:

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

This aligns the guard with its documented intent (gating by depositor address) and with the `isAllowedToDeposit` view function which also checks by depositor.

## Proof of Concept

```solidity
// Setup: pool configured with DepositAllowlistExtension.
// Pool admin allowlists trustedLP; attacker is not allowlisted.

address trustedLP = address(0xABCD);  // on the allowlist
address attacker  = address(0x1234);  // NOT on the allowlist

// Confirm attacker is not allowlisted
assertFalse(depositExt.allowedDepositor(address(pool), attacker));

// Attacker calls addLiquidity with owner = trustedLP
vm.prank(attacker);
pool.addLiquidity(
    trustedLP,   // owner — passes the allowlist check (wrong identity checked)
    0,           // salt
    deltas,
    callbackData,
    extensionData
);

// Attacker's metricOmmSwapCallback delivers tokens.
// LP shares minted to trustedLP; pool bin state modified by unauthorized depositor.
// Attacker bypassed the deposit allowlist entirely.
assertFalse(depositExt.allowedDepositor(address(pool), attacker)); // still not allowlisted
// but pool state has changed — deposit succeeded
```