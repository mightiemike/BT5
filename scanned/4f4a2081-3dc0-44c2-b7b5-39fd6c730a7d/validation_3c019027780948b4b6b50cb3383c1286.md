### Title
`DepositAllowlistExtension` gates on caller-supplied `owner` instead of actual `sender`, allowing any address to bypass the deposit allowlist - (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the allowlist against the `owner` parameter (a value freely supplied by the caller of `addLiquidity`) rather than the actual `sender` (the real caller). Any unprivileged address can bypass the deposit allowlist by specifying an already-allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

The pool calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both the real caller and the caller-chosen owner to the extension. [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`, the real caller) is silently discarded (unnamed `address`), and the allowlist check is performed against `owner`: [3](#0-2) 

Because `owner` is a free parameter in `addLiquidity`, any caller can pass `owner = some_allowlisted_address` and the check `allowedDepositor[msg.sender][owner]` will return `true`, bypassing the guard entirely.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the real caller, i.e., `msg.sender` of the pool's `swap()` call): [4](#0-3) 

This asymmetry confirms the bug is specific to `DepositAllowlistExtension`.

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism to restrict who may provide liquidity to a pool. With this bug, any address — regardless of allowlist status — can call `pool.addLiquidity(owner=allowlisted_address, ...)` and pass the guard. The LP position is credited to the allowlisted `owner` (not the attacker), and the attacker's tokens are consumed by the pool. The consequences are:

1. **Allowlist invariant broken**: The pool admin's access control is completely ineffective. Any actor can deposit into a restricted pool.
2. **Forced liquidity injection**: An attacker can inject liquidity into specific bins credited to an allowlisted address without that address's consent, altering the pool's liquidity distribution and potentially affecting LP returns for legitimate participants.
3. **Griefing / pool state manipulation**: By targeting specific bins and salts, an attacker can interfere with an allowlisted LP's expected position state.

`removeLiquidity` enforces `msg.sender == owner`, so the attacker cannot reclaim the deposited tokens — the loss is one-directional and permanent for the attacker, but the pool's access control invariant is irreversibly broken per deposit. [5](#0-4) 

### Likelihood Explanation

- The `owner` parameter in `addLiquidity` is completely unconstrained; no validation prevents an arbitrary address from being supplied.
- Allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` reads.
- No special privilege or precondition is required — any EOA or contract can execute the bypass in a single transaction.
- The `SwapAllowlistExtension` and `DepositAllowlistExtension` are both production periphery contracts intended for real pool deployments.

### Recommendation

Change `beforeAddLiquidity` to gate on the first parameter (`sender`, the real caller) rather than `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which gates on `sender` (the real pool caller).

### Proof of Concept

```solidity
// Pool is deployed with DepositAllowlistExtension.
// Only `allowedLP` is allowlisted; `attacker` is not.

// Attacker bypasses the allowlist by supplying allowedLP as owner:
vm.prank(attacker);
pool.addLiquidity(
    allowedLP,          // owner = allowlisted address — passes the guard
    someArbitrarySalt,
    deltas,
    callbackData,       // attacker pays tokens via callback
    ""
);
// Result: deposit succeeds despite attacker not being allowlisted.
// LP shares are credited to allowedLP; attacker's tokens are consumed.
// The allowlist guard is fully bypassed.
```

The allowlist check `allowedDepositor[pool][allowedLP]` returns `true`, so the `NotAllowedToDeposit` revert is never triggered, even though the actual depositor (`attacker`) is not on the allowlist. [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L91-98)
```text
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L12-42)
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
  }

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
