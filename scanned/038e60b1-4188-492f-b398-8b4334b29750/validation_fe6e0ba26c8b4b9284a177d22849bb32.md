### Title
`DepositAllowlistExtension` Guards on `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook validates the `owner` parameter (the address that will own the liquidity position) rather than the `sender` parameter (the address that actually calls `addLiquidity`). Because `owner` is a free caller-supplied argument in `MetricOmmPool.addLiquidity`, any unprivileged address can bypass the deposit allowlist entirely by naming an already-allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-controlled `owner` argument and forwards both `msg.sender` (as `sender`) and `owner` to the `beforeAddLiquidity` hook: [1](#0-0) 

The extension hook receives `sender` as its first parameter but silently discards it (the parameter is unnamed `address`). It then checks only `owner`: [2](#0-1) 

The guard evaluates `allowedDepositor[msg.sender][owner]` — where `msg.sender` is the pool and `owner` is the attacker-supplied second argument — so the check passes whenever the named `owner` is allowlisted, regardless of who is actually calling `addLiquidity`.

By contrast, the sibling `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the first parameter, which equals `msg.sender` of the `swap` call): [3](#0-2) 

The asymmetry confirms the `DepositAllowlistExtension` is checking the wrong identity.

The token payment for the position is pulled from the **caller** via the swap callback, not from `owner`: [4](#0-3) 

`removeLiquidity` enforces `msg.sender == owner`: [5](#0-4) 

So the position is irrevocably owned by the named `owner`, not the caller.

### Impact Explanation

A pool admin deploys a permissioned pool (e.g., KYC-gated, institutional-only) and configures `DepositAllowlistExtension` to restrict depositors. The guard is silently inoperative: any address — including one that has never been KYC'd or approved — can inject liquidity into the pool by naming any allowlisted address as `owner`. The allowlisted address receives a position it did not request and can withdraw the tokens; the unauthorized caller loses the deposited tokens. The pool admin's core access-control invariant ("only approved addresses may add liquidity") is broken by an unprivileged, zero-privilege call path. This constitutes an admin-boundary break where a factory/pool-admin-configured guard is bypassed by an unprivileged path.

### Likelihood Explanation

The bypass requires no special role, no flash loan, and no oracle manipulation. Any EOA or contract can execute it in a single transaction by supplying any allowlisted address as the `owner` argument to `addLiquidity`. The allowlist of approved depositors is publicly readable on-chain (`allowedDepositor` is a public mapping), so an attacker can trivially identify a valid `owner` to name.

### Recommendation

Change `beforeAddLiquidity` to validate `sender` (the first parameter — the actual caller of `addLiquidity`) instead of `owner`:

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

This mirrors the pattern already used correctly in `SwapAllowlistExtension.beforeSwap`.

### Proof of Concept

1. Pool admin creates a pool with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)`. Bob is **not** allowlisted.
3. Bob calls `pool.addLiquidity(owner = Alice, salt = 0, deltas = ..., callbackData = ..., extensionData = ...)`.
4. The pool calls `DepositAllowlistExtension.beforeAddLiquidity(Bob, Alice, ...)`.
5. The extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` pulls tokens from Bob via the swap callback and records the position under Alice's key.
7. Alice calls `pool.removeLiquidity(owner = Alice, ...)` and withdraws the tokens.
8. Bob has deposited into a pool he was never authorized to touch; the allowlist provided zero protection.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
