Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual token provider, i.e., `msg.sender` of the pool call) and instead validates `owner` (the caller-supplied position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unprivileged address can bypass the deposit allowlist by supplying any allowlisted address as `owner`, depositing tokens into a curated pool and injecting liquidity into an arbitrary position without consent.

## Finding Description

**Root cause — wrong actor bound in the guard:**

`DepositAllowlistExtension.beforeAddLiquidity` discards its first argument (`sender`) and checks `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Inside the extension, `msg.sender` is the pool, so the effective check is `allowedDepositor[pool][owner]`. `owner` is a free caller-supplied parameter with no ownership proof.

**No `msg.sender == owner` constraint in `addLiquidity`:**

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as separate arguments to the extension, but never enforces that they are equal:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

Compare with `removeLiquidity`, which correctly enforces `msg.sender == owner` before proceeding:

```solidity
// metric-core/contracts/MetricOmmPool.sol L206
if (msg.sender != owner) revert NotPositionOwner();
```

No equivalent guard exists in `addLiquidity`.

**Correct pattern exists in `SwapAllowlistExtension`:**

`SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual swapper), not the recipient:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

**Exploit path:**

1. Pool is configured with `DepositAllowlistExtension`; `allowedDepositor[pool][alice] = true`, `allowedDepositor[pool][bob] = false`.
2. Bob (non-allowlisted) calls `pool.addLiquidity(owner=alice, salt=99, deltas=<bob's bins>, callbackData=<bob's approval>, extensionData="")`.
3. `_beforeAddLiquidity(msg.sender=bob, owner=alice, ...)` is called; the extension checks `allowedDepositor[pool][alice]` → `true` → guard passes.
4. `LiquidityLib.addLiquidity` is called via DELEGATECALL; inside it, `msg.sender` is Bob (the original external caller), so the liquidity callback `IMetricOmmModifyLiquidityCallback(bob).metricOmmModifyLiquidityCallback(...)` pulls Bob's tokens.
5. Alice's position `(alice, 99)` receives the injected liquidity in bins Bob chose; Bob has deposited into a curated pool without being allowlisted.

**Existing guards are insufficient:** The only guard is the extension check itself, which is the broken component. There is no `msg.sender == owner` check in `addLiquidity` and no secondary validation of the token provider's identity.

## Impact Explanation

**Admin-boundary break:** The deposit allowlist is an admin-configured access control (e.g., KYC, institutional-only curation). Any unprivileged address can bypass it entirely by supplying any allowlisted address as `owner`. The pool admin's curation policy is rendered ineffective.

**Unauthorized liquidity injection:** Because positions are keyed by `(owner, salt)`, an attacker can inject liquidity into an existing LP's position in bins the LP did not choose. The LP cannot prevent this and must remove the injected liquidity after the fact, potentially after suffering impermanent loss in the injected bins.

**Pool receives tokens from unauthorized sources:** The pool's token balances grow from callers explicitly excluded by the pool admin, violating the invariant that only allowlisted depositors contribute to pool state.

## Likelihood Explanation

`addLiquidity` is a public external function requiring no special role, permit, or setup. Allowlisted addresses are discoverable on-chain via `allowedDepositor` mapping events or direct storage reads. The attacker bears the token cost (tokens go to the victim's position), which limits financial incentive for pure bypass, but the curation failure and griefing vector (injecting unwanted bin exposure into another LP's position) are fully reachable with no economic barrier beyond gas.

## Recommendation

Check `sender` (the actual token provider) instead of `owner` (the position recipient), consistent with how `SwapAllowlistExtension.beforeSwap` handles the analogous case:

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

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true   // alice is allowlisted
  allowedDepositor[pool][bob]   = false  // bob is NOT allowlisted
  bob implements IMetricOmmModifyLiquidityCallback

Attack (bob calls directly):
  pool.addLiquidity(
      owner        = alice,   // allowlisted — satisfies the guard
      salt         = 99,
      deltas       = <bins bob wants to inject>,
      callbackData = <bob's token approval>,
      extensionData = ""
  )

Extension check (L38, DepositAllowlistExtension.sol):
  allowedDepositor[pool][alice] == true  →  guard passes

LiquidityLib.addLiquidity (via DELEGATECALL):
  msg.sender == bob  →  callback pulls bob's tokens
  position key = (alice, 99)  →  alice's position receives injected liquidity

Outcome:
  - Bob deposited into a curated pool without being allowlisted
  - Alice's position contains bins she never chose
  - Pool admin's curation policy is fully bypassed
```

Foundry test plan: deploy pool with `DepositAllowlistExtension`, allowlist only `alice`, have `bob` (implementing the callback) call `addLiquidity` with `owner=alice`, assert the call succeeds and `alice`'s position shares increase while `bob`'s tokens are transferred to the pool.