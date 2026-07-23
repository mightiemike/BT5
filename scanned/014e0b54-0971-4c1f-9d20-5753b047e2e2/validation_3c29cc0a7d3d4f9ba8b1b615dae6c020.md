The vulnerability is real. Here is the analysis:

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any non-allowlisted address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates on `owner` instead. Because `addLiquidity` lets any caller freely choose the `owner` parameter, a non-allowlisted attacker can pass an allowlisted address as `owner` and the guard passes unconditionally.

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension with `msg.sender` as `sender` and the caller-supplied `owner` as the LP-position recipient: [1](#0-0) 

The extension hook signature receives both `sender` and `owner`, but the implementation drops `sender` (unnamed first parameter) and checks only `owner`: [2](#0-1) 

The guard is `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool and `owner` is the attacker-controlled argument. The actual depositor (`sender` / `msg.sender` in the pool) is never consulted.

### Impact Explanation
Any address can add liquidity to a pool that has `DepositAllowlistExtension` configured, simply by supplying an allowlisted address as `owner`. The attacker pays the tokens (via the `addLiquidityCallback`) and the LP shares are minted to the allowlisted address. The deposit allowlist — the entire purpose of this extension — is completely bypassed. This breaks core pool access-control functionality and allows unrestricted token deposits into pools that pool admins intended to restrict.

### Likelihood Explanation
The `addLiquidity` function is fully public with no other access control. The `owner` parameter is entirely caller-controlled with no validation. Any attacker who knows an allowlisted address (which is on-chain readable via `allowedDepositor`) can exploit this immediately.

### Recommendation
Check `sender` (the actual depositor / `msg.sender` in the pool), not `owner` (the LP-position recipient). The fix is one character change:

```solidity
// current (wrong): checks owner
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// fixed: checks sender
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

### Proof of Concept
```solidity
// pool has DepositAllowlistExtension; only address A is allowlisted
// attacker is address B (not allowlisted)

// B calls:
pool.addLiquidity(
    /*owner=*/ A,      // allowlisted address — passes the guard
    /*salt=*/  0,
    deltas,
    callbackData,      // B pays tokens here
    extensionData
);
// extension checks allowedDepositor[pool][A] == true → passes
// B's tokens are deposited; A receives LP shares
// deposit allowlist is fully bypassed
``` [2](#0-1) [3](#0-2)

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
