Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates LP-Share Recipient (`owner`) Instead of Token Provider (`sender`), Allowing Non-Allowlisted Addresses to Bypass the Deposit Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` argument and checks only `owner` against the allowlist. Because `msg.sender` inside the hook is the pool, the effective check is `allowedDepositor[pool][owner]`. Any address that is not on the allowlist can deposit tokens into a restricted pool by nominating an allowlisted address as `owner`, fully bypassing the deposit guard.

## Finding Description
The hook at `DepositAllowlistExtension.sol` L32–42 silently discards the first parameter (`sender`, the actual token provider) and gates only `owner` (the LP-share recipient):

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

`MetricOmmPool.addLiquidity` (L191) calls `_beforeAddLiquidity(msg.sender, owner, ...)`, and `ExtensionCalling._beforeAddLiquidity` (L97) correctly forwards both `sender` and `owner` to the extension via `abi.encodeCall`. The extension receives `sender` as its first argument but ignores it entirely.

**Direct pool path:** `bob` (not allowlisted) calls `pool.addLiquidity(owner=alice, ...)`. The pool passes `sender=bob, owner=alice` to the hook. The hook evaluates `allowedDepositor[pool][alice]` → `true` → no revert. Bob's tokens are pulled via the callback; LP shares are minted to alice.

**LiquidityAdder path:** `addLiquidityExactShares(pool, owner=alice, ...)` stores `payer=msg.sender` (bob) in transient context and calls `pool.addLiquidity(positionOwner=alice, ...)`. The hook again sees `owner=alice` and passes. Bob's tokens are pulled from transient payer context; alice receives LP shares.

The `allowedDepositor` mapping, `setAllowedToDeposit`, and `isAllowedToDeposit` are all keyed by `depositor`, and the contract is named `DepositAllowlistExtension` — all naming signals that the gated identity is the token provider, not the LP-share recipient. The first parameter being unnamed and discarded is the root cause.

## Impact Explanation
The deposit allowlist — the sole on-chain mechanism for restricting who may provide liquidity to a pool — is fully bypassable by any address that knows one allowlisted address (readable from the public `allowedDepositor` mapping or emitted events). Tokens from unauthorized sources enter the pool, breaking the pool admin's access-control invariant. The non-allowlisted depositor's tokens are locked in the allowlisted owner's LP position, constituting direct loss of user principal. Severity: **Medium** — access-control bypass with direct token loss for the depositor; no protocol-level insolvency, but the pool's restricted-access property is entirely negated.

## Likelihood Explanation
Exploitable by any unprivileged address with no special setup beyond knowing one allowlisted address. Works through both the direct pool call and the public `MetricOmmPoolLiquidityAdder` path. No admin cooperation, malicious token, or flash loan required. The `allowedDepositor` mapping is public, making allowlisted addresses trivially discoverable on-chain.

## Recommendation
Replace the unnamed first parameter with `sender` and gate on it instead of `owner`:

```solidity
function beforeAddLiquidity(
    address sender,   // actual token provider
    address owner,
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the payer and the LP-share recipient, check both. Update `setAllowedToDeposit`, `isAllowedToDeposit`, and all documentation to reflect which identity is gated. The NatSpec in `IMetricOmmPoolActions` L147 ("owner must pass allowlist") must also be corrected to match the fixed behavior.

## Proof of Concept

```solidity
// Setup: pool configured with DepositAllowlistExtension
// allowedDepositor[pool][alice] = true
// allowedDepositor[pool][bob]   = false (default)
// bob holds token0 and token1, approved to the pool

// Bob calls addLiquidity directly, naming alice as owner:
pool.addLiquidity(
    alice,        // owner ← allowlisted, hook passes
    someSalt,
    delta,        // bob's tokens
    callbackData,
    extensionData
);
// Result:
// - hook checks allowedDepositor[pool][alice] → true → no revert
// - bob's tokens transferred into pool via callback to bob
// - LP shares minted to alice
// - bob bypassed the deposit allowlist; tokens locked in alice's LP position

// Via LiquidityAdder (bob calls):
liquidityAdder.addLiquidityExactShares(
    pool, alice, salt, deltas, max0, max1, extensionData
);
// Same result: hook sees owner=alice, passes; bob's tokens pulled as payer
```