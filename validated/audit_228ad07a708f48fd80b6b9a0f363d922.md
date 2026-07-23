### Title
`DepositAllowlistExtension` checks position owner instead of actual depositor, allowing unauthorized deposits — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller of `addLiquidity`) and instead checks the `owner` argument (who receives the LP position). Because `addLiquidity` accepts an arbitrary `owner` address, any unauthorized user can bypass the deposit allowlist by specifying an allowlisted address as `owner`.

### Finding Description
The extension is documented as "Gates `addLiquidity` by depositor address, per pool." The pool calls the hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The first argument is the actual caller (`msg.sender` of `addLiquidity`); the second is the `owner` parameter supplied by that caller. Inside the hook:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The first `address` (the real depositor/sender) is unnamed and discarded. The guard checks `owner` — an address freely chosen by the caller. Because `addLiquidity` imposes no restriction on who may be named as `owner`, any caller can pass an allowlisted address as `owner` and satisfy the check.

The `removeLiquidity` path enforces `msg.sender == owner`:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [3](#0-2) 

So the unauthorized depositor's tokens are permanently transferred into the pool under the allowlisted address's position, which that address can then withdraw.

### Impact Explanation
The deposit allowlist is completely bypassed. Unauthorized users can inject funds into a curated pool by naming any allowlisted address as `owner`. The allowlisted address receives an unsolicited LP position (griefing vector). The pool admin's curation policy — the sole purpose of deploying this extension — is rendered ineffective. This breaks core pool functionality for curated pools and constitutes an admin-boundary break via an unprivileged path.

### Likelihood Explanation
High. Allowlisted addresses are visible on-chain via `AllowedToDepositSet` events. Any unauthorized user can exploit this in a single transaction with no special privileges, no flash loan, and no complex setup.

### Recommendation
Replace the `owner` check with the `sender` argument (the actual caller):

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the actual swap initiator):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

### Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension` configured in `beforeAddLiquidity` order.
2. Pool admin allowlists `bob` but not `alice`: `extension.setAllowedToDeposit(pool, bob, true)`.
3. `alice` (unauthorized) calls `pool.addLiquidity(bob, salt, deltas, callbackData, extensionData)`.
4. The hook receives `sender = alice` (discarded), `owner = bob`.
5. `allowedDepositor[pool][bob]` → `true` → check passes.
6. Alice's tokens are deposited; the LP position is attributed to `bob`.
7. `bob` calls `removeLiquidity(bob, salt, deltas, extensionData)` and withdraws Alice's tokens.
8. Alice has permanently lost her tokens; the allowlist was completely bypassed. [2](#0-1) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-195)
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
