Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of the `addLiquidity` call) and instead validates the caller-supplied `owner` argument. Because `owner` is a free parameter chosen by the caller, any address not on the allowlist can bypass the guard by nominating any allowlisted address as `owner`, while the real depositor — who supplies the tokens — is never checked. This voids the pool admin's access-control boundary for every `addLiquidity` call where `owner` is allowlisted but `sender` is not.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly forwards both `sender` and `owner` to the extension hook via `abi.encodeCall`: [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter (the `sender`) as an unnamed wildcard `address,` and checks `owner` instead: [3](#0-2) 

The parallel guard `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender`, discarding the second parameter (`recipient`): [4](#0-3) 

The two guards are structurally identical in intent but check different identities. Because `owner` is freely chosen by the caller and `allowedDepositor` is publicly readable on-chain, any unauthorized address can trivially construct a bypass by passing any allowlisted address as `owner`.

## Impact Explanation
The deposit allowlist is an admin-configured access-control boundary intended to restrict which addresses may supply liquidity (e.g., KYC/AML compliance, institutional-only pools). Because the guard checks `owner` rather than `sender`, any address not on the allowlist can call `addLiquidity(allowlistedAddress, salt, deltas, …)`, pass the guard, and inject liquidity into a restricted pool. The actual token transfer is executed by the unauthorized caller via the liquidity callback; the position is credited to the allowlisted `owner`. This is a direct admin-boundary break: an unprivileged path bypasses an admin-configured extension guard, violating the invariant that only allowlisted addresses may deposit into a restricted pool.

## Likelihood Explanation
The bypass requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with an allowlisted `owner`. The allowlisted addresses are publicly readable from `allowedDepositor`. The attack is trivially constructable from on-chain data alone and is reachable through the standard `addLiquidity` entry point with no preconditions beyond knowing one allowlisted address.

## Recommendation
Replace the discarded `address,` wildcard with a named `sender` parameter and check it instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

## Proof of Concept
1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only address `A` via `setAllowedToDeposit(pool, A, true)`.
2. Unauthorized address `B` (not on the allowlist) calls `pool.addLiquidity(A /*owner*/, salt, deltas, callbackData, extensionData)`.
3. The pool calls `DepositAllowlistExtension.beforeAddLiquidity(B /*sender*/, A /*owner*/, …)`.
4. The guard evaluates `allowedDepositor[pool][A]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; the pool calls `B`'s callback to collect tokens.
6. The position is minted to `A`; `B` has successfully deposited into a pool it was explicitly excluded from.
7. The pool admin's allowlist invariant — "only allowlisted addresses may supply liquidity" — is broken without any privileged action.

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
