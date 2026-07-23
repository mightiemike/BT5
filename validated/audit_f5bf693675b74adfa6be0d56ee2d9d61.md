Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`, who provides tokens via callback) and instead validates `owner` (the position-credit recipient, a freely caller-supplied argument). Because `owner` is unconstrained, any non-allowlisted address can pass an allowlisted address as `owner` and bypass the guard entirely, injecting liquidity into a restricted pool without authorization.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument to the hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  actual depositor  caller-supplied recipient
```

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional parameter (`sender`) is unnamed and discarded; only `owner` is checked:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`owner` is a parameter of `addLiquidity` with no constraint that it equals `msg.sender`. The sibling `SwapAllowlistExtension.beforeSwap` demonstrates the correct pattern — it checks `sender`, not the recipient:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-38
function beforeSwap(address sender, address, ...)
    ...
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

`DepositAllowlistExtension` is inconsistent with this established pattern and with its own NatSpec ("Gates `addLiquidity` by depositor address").

## Impact Explanation
An unprivileged address can inject liquidity into any bin of a restricted pool by supplying an allowlisted address as `owner`. The position is credited to that allowlisted address (so the attacker cannot directly withdraw it), but the attacker controls which bins receive liquidity and how much, enabling manipulation of the pool's bin cursor and marginal price. This distorts the bid/ask spread for all subsequent swaps, harming existing LPs through adverse price execution. The pool admin's sole mechanism for controlling who can alter pool liquidity composition is rendered ineffective — a confirmed admin-boundary break with indirect fund impact on existing LPs.

## Likelihood Explanation
Exploitation requires only knowing one allowlisted address, which is observable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` reads. No special privileges, flash loans, or oracle manipulation are needed. The attacker must be able to implement the `IMetricOmmModifyLiquidityCallback` interface and supply tokens. Likelihood is high whenever the pool has at least one allowlisted depositor and the attacker holds sufficient tokens.

## Recommendation
Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// Before (wrong):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (correct):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

Also update `setAllowedToDeposit` / `isAllowedToDeposit` documentation to reflect that the key is the caller of `addLiquidity`, not the position owner.

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension`; pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** allowlisted.
2. Bob calls `pool.addLiquidity(alice, 0, deltas, callbackData, "")`.
3. Pool calls `extension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
4. Guard evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. Pool calls `IMetricOmmModifyLiquidityCallback(bob).metricOmmModifyLiquidityCallback(...)` — Bob transfers tokens into the pool.
6. Liquidity is minted into the targeted bins; position is credited to Alice.
7. Bob has injected liquidity into the restricted pool without being on the allowlist, bypassing the guard entirely. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
