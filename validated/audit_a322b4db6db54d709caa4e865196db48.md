### Title
`DepositAllowlistExtension.beforeAddLiquidity` Guards `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` argument and checks the `owner` argument instead. Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` with no `msg.sender == owner` constraint, any unprivileged caller can satisfy the allowlist check by naming an already-allowlisted address as `owner`, depositing tokens into the pool while the guard believes the allowlisted party is acting.

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`sender` = `msg.sender` (the actual on-chain caller); `owner` = the caller-supplied LP-position owner. There is no `require(msg.sender == owner)` in `addLiquidity` (that constraint exists only in `removeLiquidity`). [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first parameter is unnamed) and evaluates `owner` against the allowlist:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The NatSpec, admin setter, and view helper all use the word **depositor**, implying the check should target the actual caller: [4](#0-3) 

### Impact Explanation

An address not in the allowlist calls:

```
pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)
```

The extension sees `owner = allowlistedAddress`, passes the guard, and the pool executes the full liquidity addition. Tokens are pulled from the attacker via the swap callback; the LP position is minted to `allowlistedAddress`. The deposit allowlist — the sole access-control mechanism for liquidity addition on pools that deploy this extension — is rendered completely ineffective. Any actor can deposit into a restricted pool, bypassing whatever KYC, compliance, or manipulation-prevention policy the pool admin intended to enforce.

### Likelihood Explanation

The bypass requires no special privilege: any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The only prerequisite is knowing one allowlisted address (trivially observable on-chain via `AllowedToDepositSet` events or the public `allowedDepositor` mapping). The attack is a single transaction with no setup cost.

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the actual swap caller): [5](#0-4) 

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` in the `BEFORE_ADD_LIQUIDITY_ORDER` slot.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** allowlisted.
3. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. Pool calls `_beforeAddLiquidity(bob, alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → guard passes.
6. `LiquidityLib.addLiquidity` executes; tokens are pulled from Bob via callback; LP shares are minted to Alice.
7. Bob has successfully deposited into a pool he is not authorized to touch. The allowlist is bypassed. [3](#0-2) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-29)
```text
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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
