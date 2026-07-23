After reading the pool, extension, and liquidity-adder contracts in full, I traced the exact call path for the deposit allowlist and found a real identity-mismatch analog.

---

### Title
`DepositAllowlistExtension` gates on `owner` (LP-share recipient) instead of `sender` (actual depositor), allowing any unprivileged address to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and checks only `owner` against the per-pool allowlist. Because `MetricOmmPool.addLiquidity` lets any caller supply an arbitrary `owner`, an address that is not on the allowlist can add liquidity to a restricted pool by naming an allowlisted address as `owner`. The pool's access-control invariant — "only allowlisted depositors may add liquidity" — is broken.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`msg.sender` (the actual depositor / payer) is passed as `sender`; the caller-supplied `owner` (LP-share recipient) is a separate argument.

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the `sender` parameter is unnamed and never read. Only `owner` is checked:

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

The NatDoc for the contract states: *"Gates `addLiquidity` by depositor address, per pool."* The `setAllowedToDeposit` setter names its parameter `depositor`. The intent is to gate the calling depositor, but the implementation gates the LP-share recipient. [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly reads and checks `sender` (the actual swapper):

```solidity
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The asymmetry confirms the deposit extension has the wrong field.

---

### Impact Explanation

Any unprivileged address can call `pool.addLiquidity(owner = allowlistedAddress, ...)` directly, or route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner = allowlistedAddress, ...)`: [5](#0-4) 

The extension sees `allowedDepositor[pool][allowlistedAddress] == true` and passes. LP shares are minted to `allowlistedAddress`; tokens are pulled from the actual caller. The deposit allowlist — an admin-configured access-control boundary — is fully bypassed by any unprivileged path. A pool intended to be private (e.g., for regulatory or business reasons) accepts liquidity from arbitrary addresses, breaking the core invariant the admin deployed the extension to enforce.

---

### Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no price manipulation. Any EOA or contract can execute it in a single transaction by supplying an allowlisted address as `owner`. The `MetricOmmPoolLiquidityAdder` makes this even simpler because it already separates `owner` from `msg.sender` (payer) as a first-class parameter. [6](#0-5) 

---

### Recommendation

In `DepositAllowlistExtension.beforeAddLiquidity`, read and check `sender` (the actual depositor / caller) instead of — or in addition to — `owner`:

```solidity
// current (wrong)
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) { ... }

// fixed
function beforeAddLiquidity(address sender, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender]   // gate the actual caller
        && !allowedDepositor[msg.sender][owner]) { // optionally also gate the recipient
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

If the intent is strictly to gate the calling depositor (as the NatDoc states), only `sender` should be checked.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured in `beforeAddLiquidity` order.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** allowlisted.
3. Bob calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)` directly.
4. Pool calls `_beforeAddLiquidity(msg.sender = Bob, owner = alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice] == true` → does **not** revert.
6. `LiquidityLib.addLiquidity` mints LP shares to `alice`; Bob's callback pays the tokens.
7. Bob has successfully added liquidity to a pool that was supposed to block him. The deposit allowlist is bypassed. [2](#0-1) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-19)
```text
/// @title DepositAllowlistExtension
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L52-68)
```text
  /// @dev `msg.sender` is always the payer for token pulls in callback (stored in transient settlement context).
  /// @param owner Position owner recorded by the pool.
  /// @param maxAmountToken0 Max token0 (native units) the pool may request; inclusive check before pull.
  /// @param maxAmountToken1 Max token1 (native units) the pool may request; inclusive check before pull.
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
