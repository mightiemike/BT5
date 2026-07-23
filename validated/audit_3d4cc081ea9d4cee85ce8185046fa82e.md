Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual on-chain caller who provides tokens) and validates `owner` instead — a free caller-supplied parameter in `MetricOmmPool.addLiquidity`. Because any caller can set `owner` to any allowlisted address, the allowlist provides zero protection: an unprivileged address can inject liquidity into a restricted pool by naming any allowlisted address as `owner`.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both values via `abi.encodeCall`: [2](#0-1) 

Inside the extension, the first parameter (`sender`) is unnamed and discarded; only `owner` is checked against `allowedDepositor`: [3](#0-2) 

The contract's own NatSpec states the intended invariant — gating by **depositor address** — which is broken: [4](#0-3) 

Since `owner` is a free argument supplied by the caller in `addLiquidity(address owner, ...)`, any address can pass `owner = <allowlisted_address>` and the guard approves the call. The actual depositor (`msg.sender` of the pool call, i.e., `sender`) is never checked. There are no other guards in the call path that validate the actual depositor identity.

## Impact Explanation

The deposit allowlist is rendered completely ineffective. Any address — regardless of allowlist status — can inject liquidity into a pool that is supposed to be restricted (KYC-gated, institutional-only, whitelist-only). The unauthorized depositor supplies the tokens; the named allowlisted `owner` receives the LP position without consent. This constitutes an admin-boundary break (an unprivileged path bypasses a pool admin-configured access control) and broken core pool functionality. The named `owner` also receives unsolicited LP exposure — if the pool is adversarial or the oracle is later manipulated, the named owner bears the loss without having authorized the deposit.

## Likelihood Explanation

Trivially exploitable with a single transaction. All allowlisted addresses are visible on-chain via `allowedDepositor` storage reads or `AllowedToDepositSet` events. No special privileges, flash loans, or multi-step setup are required. The attacker only needs to call `addLiquidity` with `owner` set to any allowlisted address.

## Recommendation

Validate `sender` (the actual depositor) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
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

1. Pool is deployed with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)` — only Alice is allowlisted.
3. Bob (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner = Alice,   // ← free parameter; Bob names Alice
       salt  = 0,
       deltas = <any valid delta>,
       callbackData = ...,
       extensionData = ""
   );
   ```
4. Pool calls `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` runs; Bob's tokens are pulled via the swap callback; Alice's position is credited.
7. Bob has added liquidity to a restricted pool without being on the allowlist. The allowlist guard is fully bypassed. [3](#0-2) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-12)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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
