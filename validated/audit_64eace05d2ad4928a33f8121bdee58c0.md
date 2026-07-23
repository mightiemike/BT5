Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates Caller-Supplied `owner` Instead of Actual `sender`, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead validates `owner`, a free caller-supplied argument. Because `addLiquidity` imposes no `msg.sender == owner` constraint before invoking the hook, any unprivileged address can bypass the deposit allowlist by naming an already-allowed address as `owner`, causing permanent loss of the caller's tokens and unauthorized pool state mutation.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as a separate argument into the hook: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first parameter (`sender`) and checks only `owner` against the allowlist: [2](#0-1) 

Since `owner` is a free argument with no equality enforcement in the pool, any address `bob` can call `addLiquidity(owner = alice)` where `alice` is allowlisted. The extension evaluates `allowedDepositor[pool][alice] == true` and does not revert. Bob's tokens are pulled via the swap callback, and the LP position is minted to `alice`.

`removeLiquidity` enforces `msg.sender == owner`: [3](#0-2) 

This means `bob` can never recover the tokens he paid — they are permanently locked in a position owned by `alice`.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual caller), confirming the inconsistency is a defect, not a design choice: [4](#0-3) 

## Impact Explanation

- **Admin-boundary break**: The pool admin's deposit allowlist is fully bypassed by any unprivileged caller. The invariant "only allowlisted addresses may add liquidity" is violated.
- **Permanent loss of caller's principal**: Because `removeLiquidity` requires `msg.sender == owner`, the unauthorized depositor cannot recover the tokens they paid. This is a direct, irreversible loss of user funds.
- **LP dilution**: Unauthorized liquidity injections dilute fee shares of existing LPs in targeted bins without consent.

## Likelihood Explanation

Exploitation requires only identifying one allowlisted address (trivially observable on-chain via past `AllowedToDepositSet` events) and calling `addLiquidity` with that address as `owner`. No special role, flash loan, or oracle manipulation is needed. Any EOA or contract can execute this in a single transaction.

## Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// Fixed:
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantic is "only allowed addresses may *own* LP positions," both `sender` and `owner` must be checked and admin-facing documentation updated accordingly.

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner        = alice,   // alice is allowed → check passes
      salt         = 0,
      deltas       = { binIdxs: [0], shares: [1_000_000] },
      callbackData = "",
      extensionData = ""
  )

Extension check (DepositAllowlistExtension.beforeAddLiquidity):
  sender (bob) is discarded
  allowedDepositor[pool][alice] == true → no revert

Result:
  - bob's tokens pulled via metricOmmModifyLiquidityCallback
  - LP position minted to alice
  - bob calls removeLiquidity(owner = alice) → reverts: msg.sender != owner
  - bob's tokens permanently lost; deposit allowlist fully bypassed
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
