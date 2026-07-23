Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Supplied `owner` Instead of Actual `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the real `msg.sender` of `addLiquidity`) and instead validates the caller-supplied `owner` (the LP position recipient) against the allowlist. Because `owner` is a free parameter, any unlisted address can bypass the deposit allowlist by supplying any allowlisted address as `owner`, depositing tokens via the swap callback, and forcing an LP position onto that allowlisted address.

## Finding Description
`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` and passes `msg.sender` as `sender` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both `sender` (real caller) and `owner` (position recipient) and forwards them to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it unnamed (discarded), then performs the allowlist check exclusively on `owner`: [3](#0-2) 

Since `owner` is freely chosen by the caller, any unlisted address Bob can call `pool.addLiquidity(owner = Alice, ...)` where Alice is allowlisted. The extension evaluates `allowedDepositor[pool][Alice] == true` and permits the call. Bob provides tokens via the swap callback; Alice receives the LP shares without consent. The allowlist is fully circumvented.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swap initiator), not the `recipient`: [4](#0-3) 

## Impact Explanation
The deposit allowlist is the pool admin's mechanism to restrict which addresses may add liquidity (e.g., KYC-gated or institutional-only pools). Bypassing it constitutes broken core pool access control with the following concrete consequences:

1. **Broken access control**: Any unlisted address can deposit into a restricted pool, violating the pool admin's explicit configuration.
2. **Forced LP positions**: An attacker pushes unwanted LP positions onto allowlisted addresses without their consent. The allowlisted address must actively call `removeLiquidity` to recover tokens, and `removeLiquidity` enforces `msg.sender == owner` ( [5](#0-4) ), so only the allowlisted address can exit.
3. **Pool state manipulation**: An unlisted address can shift bin liquidity distribution in a restricted pool, affecting swap prices, stop-loss watermarks, or price velocity guard state in ways the pool admin did not intend.

## Likelihood Explanation
Exploitation requires only a standard `addLiquidity` call with `owner` set to any known allowlisted address (e.g., the pool admin, a known LP, or any address visible on-chain). No special privileges, flash loans, or oracle manipulation are needed. Any address can trigger this at any time against any pool using `DepositAllowlistExtension`.

## Recommendation
Replace the `owner` check with a `sender` check in `beforeAddLiquidity`. The first (currently unnamed) parameter is the actual depositor:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

## Proof of Concept
```
Setup:
  - Pool P has DepositAllowlistExtension configured.
  - allowAllDepositors[P] = false
  - allowedDepositor[P][Alice] = true
  - Bob is NOT in the allowlist.

Attack:
  1. Bob calls P.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)
  2. Pool calls _beforeAddLiquidity(msg.sender=Bob, owner=Alice, ...)
  3. ExtensionCalling encodes (sender=Bob, owner=Alice) and calls DepositAllowlistExtension.beforeAddLiquidity
  4. Extension discards sender=Bob, checks allowedDepositor[P][Alice] == true → no revert
  5. LiquidityLib.addLiquidity credits LP shares to Alice
  6. Pool calls Bob's metricOmmSwapCallback; Bob transfers tokens to the pool
  7. Alice now holds an LP position she did not request; Bob has bypassed the allowlist

Result:
  - Bob successfully deposited into a restricted pool.
  - Alice holds an unwanted LP position (she must call removeLiquidity herself to exit).
  - The deposit allowlist invariant is broken.
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
