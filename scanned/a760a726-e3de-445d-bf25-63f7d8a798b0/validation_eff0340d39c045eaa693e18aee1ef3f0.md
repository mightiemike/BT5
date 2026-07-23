### Title
`DepositAllowlistExtension` Gates Position Owner Instead of Actual Depositor, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and checks only the `owner` (position recipient). Because `addLiquidityExactShares` lets any caller specify an arbitrary `owner`, an address that is **not** on the allowlist can deposit tokens into a restricted pool by routing through any allowlisted owner address, completely defeating the access-control invariant the pool admin configured.

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

where `msg.sender` is the actual caller of `addLiquidity` (the real depositor, or the `MetricOmmPoolLiquidityAdder` acting on their behalf) and `owner` is the position recipient.

`DepositAllowlistExtension.beforeAddLiquidity` discards the first argument entirely and only validates `owner`:

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

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the owner-specifying overload) accepts any `owner` address from the caller:

```solidity
function addLiquidityExactShares(
    address pool, address owner, uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

The `payer` stored in transient context is `msg.sender` (the real depositor), but this value is never forwarded to the extension. The extension only ever sees `owner`.

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism to restrict who may provide liquidity (e.g., KYC/compliance gating, curated LP sets). Because the check is on `owner` rather than the actual depositor:

- Any unprivileged address `A` (not on the allowlist) can call `addLiquidityExactShares(pool, B, ...)` where `B` is any allowlisted address.
- `A` pays the tokens; `B` receives the position.
- If `A` controls `B` (e.g., two wallets of the same attacker), `B` can immediately call `removeLiquidity` and recover the tokens — net effect: `A` deposited into a restricted pool with zero restriction.
- Even if `A` does not control `B`, the pool's deposit restriction is broken: unauthorized capital enters the pool, violating the invariant the admin configured.

This is an admin-boundary break: an unprivileged path bypasses a factory/pool admin access-control check.

### Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidityExactShares` with an arbitrary `owner`.
- Allowlisted addresses are discoverable on-chain from `AllowedToDepositSet` events.
- The `MetricOmmPoolLiquidityAdder` is the standard periphery entry point, so the bypass path is the normal user flow.
- Likelihood: **High** (trivially reachable, no preconditions beyond knowing one allowlisted address).

### Recommendation

Check the **actual depositor** (`sender`, the first parameter) rather than — or in addition to — `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    address pool = msg.sender;
    // Reject if neither the initiating sender nor the position owner is allowlisted.
    if (!allowAllDepositors[pool]
        && !allowedDepositor[pool][sender]
        && !allowedDepositor[pool][owner])
    {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Note that when `addLiquidity` is called through `MetricOmmPoolLiquidityAdder`, `sender` will be the LiquidityAdder contract address, not the end user. The pool should either (a) pass the real payer through `extensionData`, or (b) the allowlist should gate on `owner` only when the intent is to restrict position ownership rather than token origin.

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true   // alice is KYC'd
  allowedDepositor[pool][bob]   = false  // bob is NOT on allowlist

Attack (bob controls alice):
  1. bob calls:
       liquidityAdder.addLiquidityExactShares(
           pool,
           alice,   // owner = allowlisted address
           salt,
           deltas,
           maxToken0, maxToken1,
           ""
       )
     from bob's address.

  2. Pool calls beforeAddLiquidity(liquidityAdder, alice, ...).
     Extension checks allowedDepositor[pool][alice] → true → no revert.

  3. bob pays tokens (payer = bob via transient context).
     alice receives the position shares.

  4. alice calls removeLiquidity → tokens returned to alice (bob's other wallet).

Result: bob deposited into a pool he is not authorized to access.
        The DepositAllowlistExtension did not revert.
``` [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L61-68)
```text
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
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
  }
```
