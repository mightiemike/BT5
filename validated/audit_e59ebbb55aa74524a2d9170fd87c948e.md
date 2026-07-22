### Title
Deposit Allowlist Bypass via Payer/Owner Separation — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` (the LP position recipient) while silently ignoring `sender` (the actual caller/payer). Because `MetricOmmPool.addLiquidity` explicitly allows `msg.sender != owner` (operator pattern), any unprivileged actor can add liquidity to a curated pool by nominating an allowlisted address as `owner`, paying the tokens themselves, and receiving nothing in return — or by gifting the position to a colluding allowlisted address.

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two actor arguments: `sender` (the `msg.sender` of `pool.addLiquidity`, i.e. the actual caller/payer) and `owner` (the position owner). The extension discards `sender` entirely and only checks `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool's `addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the position owner, with no requirement that they match:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

The pool's own NatSpec documents this operator pattern explicitly:

> `msg.sender` pays but need not equal `owner` (operator pattern). [3](#0-2) 

The extension's own NatSpec states its purpose as "Gates `addLiquidity` by **depositor** address, per pool" — but the depositor (payer) is never checked. [4](#0-3) 

### Impact Explanation

A non-allowlisted actor can inject liquidity into a curated pool by calling `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` directly. The extension sees `owner = allowlistedAddress` (allowlisted → passes), while the actual payer (`msg.sender`) is never checked. The pool receives tokens from a non-allowlisted source, pool bin state is mutated (bin totals, cursor position), and the deposit allowlist policy — the sole access-control mechanism for curated pools — is fully bypassed. The allowlisted address receives an LP position it did not initiate, which it cannot prevent.

Downstream consequences include:
- Unauthorized manipulation of bin liquidity distribution, affecting oracle-derived bid/ask pricing used by every subsequent swap.
- Forced LP positions on allowlisted addresses, which can only be removed by the `owner` themselves (`removeLiquidity` enforces `msg.sender == owner`).
- Broken curation invariant: the pool admin's intent to restrict depositors to a known set is defeated by any unprivileged actor who knows one allowlisted address (e.g., from on-chain events).

### Likelihood Explanation

The attack requires no special privileges. Allowlisted addresses are discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. The attacker only needs to call `pool.addLiquidity` directly (no router or adder required) with a valid allowlisted `owner`. The attack is reachable on every curated pool that uses `DepositAllowlistExtension` without `allowAllDepositors` being set. [5](#0-4) 

### Recommendation

Check `sender` (the actual caller/payer) in addition to — or instead of — `owner`. Since the pool explicitly supports the operator pattern, the correct fix depends on the intended policy:

- **Gate the payer**: check `sender` (the first, currently ignored parameter).
- **Gate both**: require both `sender` and `owner` to be allowlisted.
- **Restrict operator pattern at the pool level**: require `msg.sender == owner` in `addLiquidity` when a deposit allowlist is active (breaking change).

The minimal fix consistent with the extension's stated purpose ("Gates `addLiquidity` by depositor address"):

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    address pool_ = msg.sender;
    if (!allowAllDepositors[pool_]
        && !allowedDepositor[pool_][sender]   // gate the actual payer
        && !allowedDepositor[pool_][owner]) { // optionally also gate owner
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension as extension1.
  - Pool admin calls setAllowedToDeposit(pool, alice, true).
  - bob is NOT allowlisted.

Attack:
  1. bob approves pool for token0/token1.
  2. bob calls pool.addLiquidity(
         owner = alice,   // allowlisted → extension passes
         salt  = 0,
         deltas = { binIdxs: [0], shares: [10_000] },
         callbackData = ...,
         extensionData = ""
     );
  3. Pool calls _beforeAddLiquidity(bob, alice, ...).
  4. Extension checks allowedDepositor[pool][alice] → true → no revert.
  5. Pool mints LP shares to alice; bob's tokens are pulled via callback.
  6. bob (non-allowlisted) has successfully added liquidity to the curated pool.
``` [6](#0-5) [1](#0-0)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-19)
```text
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
```
