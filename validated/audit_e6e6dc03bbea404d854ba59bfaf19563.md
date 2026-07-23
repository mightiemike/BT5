Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks caller-controlled `owner` instead of actual `sender`, allowing any address to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of the `addLiquidity` call) and gates access solely on `owner`, a free parameter supplied by the caller. Because `owner` is unconstrained in `MetricOmmPool.addLiquidity`, any non-allowlisted address can bypass the guard by passing any allowlisted address as `owner`, completely defeating the pool's access-restriction mechanism.

## Finding Description

`MetricOmmPool.addLiquidity` is permissionlessly callable and forwards both `msg.sender` (as `sender`) and the caller-supplied `owner` to the hook: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(address sender, address owner, ...)` but unnamed the first argument, discarding `sender` entirely, and evaluates only `allowedDepositor[msg.sender][owner]`: [2](#0-1) 

Since `owner` is a free input with no on-chain restriction, an attacker passes any allowlisted address as `owner`. The extension evaluates `allowedDepositor[pool][allowlistedAddress] == true` and passes, even though the actual depositor (`sender`) is not allowlisted.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender`: [3](#0-2) 

The asymmetry between the two extensions confirms this is a coding error, not an intentional design choice.

## Impact Explanation

`DepositAllowlistExtension` is the sole on-chain mechanism for restricting who may provide liquidity to a pool. Bypassing it means any non-allowlisted address can deposit tokens into a pool configured as access-restricted (private pool, KYC-gated pool, etc.). The pool admin's allowlist configuration is completely ineffective for every pool using this extension. This is a direct admin-boundary break: an unprivileged caller bypasses the factory/pool admin's access control, violating the invariant that only approved depositors may contribute liquidity. Additionally, a non-allowlisted depositor can create positions owned by an allowlisted address without that address's consent, creating unsolicited LP positions (griefing).

## Likelihood Explanation

- `addLiquidity` is permissionlessly callable by any address with no prerequisite.
- The `owner` parameter is a free input with no on-chain restriction.
- No special privilege, flash loan, or multi-step setup is required; a single transaction suffices.
- Every pool deploying `DepositAllowlistExtension` with a non-empty allowlist is immediately and unconditionally vulnerable.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner` (the caller-controlled position recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

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

```
Setup:
  - Pool deployed with DepositAllowlistExtension
  - Alice (0xAlice) is allowlisted: allowedDepositor[pool][alice] = true
  - Bob (0xBob) is NOT allowlisted

Attack:
  1. Bob calls pool.addLiquidity(
         owner        = alice,   // any allowlisted address
         salt         = 0,
         deltas       = <valid LiquidityDelta>,
         callbackData = <Bob pays tokens in callback>,
         extensionData = ""
     )

  2. Pool calls _beforeAddLiquidity(sender=Bob, owner=alice, ...)
     [MetricOmmPool.sol L191]

  3. Extension evaluates:
       allowedDepositor[pool][alice] == true  →  check passes
     [DepositAllowlistExtension.sol L38]

  4. Bob's tokens enter the pool; position is recorded under (alice, 0).

Result:
  - Bob (non-allowlisted) successfully deposited to a restricted pool.
  - The DepositAllowlistExtension guard is completely bypassed.
  - Alice holds an unsolicited LP position funded by Bob.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
