### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual depositor) and gates on `owner` (the LP-position recipient) instead. Any unprivileged address can bypass the allowlist by naming an already-allowlisted address as `owner`.

### Finding Description

`IMetricOmmExtensions.beforeAddLiquidity` receives two distinct address arguments: [1](#0-0) 

- `sender` — the `msg.sender` of the `addLiquidity` call (the actual depositor who pays tokens and triggers the callback)
- `owner` — the address that will own the resulting LP position

`MetricOmmPool.addLiquidity` passes them correctly: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity`, however, drops `sender` (first parameter is unnamed) and checks `owner`: [3](#0-2) 

The contract's own NatSpec and `setAllowedToDeposit` parameter name both say the intent is to gate the **depositor**: [4](#0-3) 

Because `owner` is caller-supplied and entirely unconstrained (the pool imposes no restriction on who may be named as `owner` in `addLiquidity`), any address can satisfy the allowlist check by passing any already-allowlisted address as `owner`. The actual depositor (`sender`) is never validated.

Compare with `SwapAllowlistExtension`, which correctly checks `sender` and discards `recipient`: [5](#0-4) 

### Impact Explanation

The deposit allowlist guard is completely ineffective. Any address — regardless of allowlist status — can deposit into a pool that the admin intended to restrict. This breaks the core access-control invariant for liquidity provision and constitutes an admin-boundary break: an unprivileged path bypasses a pool-admin-configured guard. Pools relying on this extension for KYC, whitelist, or compliance gating receive no protection.

### Likelihood Explanation

The bypass requires only a single `addLiquidity` call with any allowlisted address as `owner`. No special privileges, flash loans, or multi-step setup are needed. The allowlisted `owner` receives the LP shares (not the attacker), so the attacker's direct financial incentive is limited, but the pool's access-control invariant is broken for every deposit made this way.

### Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`:

```solidity
// Before (wrong):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// After (correct):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is meant to deposit.
3. Attacker (`bob`, not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
5. Extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. `bob` successfully deposits; LP shares are credited to `alice`. The allowlist is bypassed. [3](#0-2) [6](#0-5)

### Citations

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-19)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
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
