Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of actual depositor `sender`, allowing full allowlist bypass — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead checks the `owner` parameter, which any caller can freely set to any address. Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, an unprivileged caller can name any allowlisted address as `owner`, pass the allowlist check, and deposit tokens into a restricted pool — completely defeating the access-control intent of the extension.

## Finding Description
`MetricOmmPool.addLiquidity` passes the actual caller as the first argument to the hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

Unlike `removeLiquidity`, which enforces `if (msg.sender != owner) revert NotPositionOwner()`, `addLiquidity` places no restriction on the relationship between `msg.sender` and `owner`. [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(address /*sender*/, address owner, ...)` but ignores the first argument entirely and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The allowlist mapping is keyed by depositor address: [4](#0-3) 

So `allowedDepositor[pool][owner]` is checked — not `allowedDepositor[pool][sender]`. An attacker (Bob) calls `pool.addLiquidity(owner = Alice, ...)` where Alice is allowlisted. The extension evaluates `allowedDepositor[pool][Alice] == true` and passes. Bob supplies the tokens via the add-liquidity callback; Alice receives the position. Bob has deposited into a pool he is not authorized to touch.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks the `sender` parameter (the actual swap caller): [5](#0-4) 

## Impact Explanation
The `DepositAllowlistExtension` is the sole access-control guard on `addLiquidity` for pools that configure it. Its bypass means any unprivileged address can add liquidity to a pool intended to be restricted to KYC'd LPs, whitelisted counterparties, or other controlled depositors. This is a broken core pool access-control path: the pool admin's restriction is completely ineffective, and unauthorized token flows enter the pool. This constitutes broken core pool functionality causing unauthorized fund flows, meeting the contest's allowed impact criteria.

## Likelihood Explanation
The bypass requires only:
1. Knowledge of one allowlisted address — observable on-chain from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. [6](#0-5) 
2. The ability to call `pool.addLiquidity(owner = allowlisted_address, ...)` and supply tokens via the callback.

No special privileges, flash loans, or complex setup are required. Any EOA or contract can execute this in a single transaction, and it is repeatable indefinitely.

## Recommendation
Change `beforeAddLiquidity` to check the `sender` parameter (the actual depositor) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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
```solidity
// Setup: pool admin allows only Alice to deposit
depositAllowlist.setAllowedToDeposit(pool, alice, true);
// alice is allowlisted; bob is NOT

// Bob bypasses the allowlist by naming Alice as owner
vm.startPrank(bob);
token0.approve(address(pool), type(uint256).max);
token1.approve(address(pool), type(uint256).max);

// Extension checks allowedDepositor[pool][alice] = true → passes
// Bob provides tokens via metricOmmAddLiquidityCallback
// Alice receives the position; Bob's tokens enter the pool
pool.addLiquidity(
    alice,   // owner = alice (allowlisted) — bypasses the check
    0,       // salt
    deltas,  // liquidity delta
    "",      // callbackData
    ""       // extensionData
);
vm.stopPrank();

// Bob successfully deposited into a pool he is not authorized to touch.
// DepositAllowlistExtension did not revert.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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
