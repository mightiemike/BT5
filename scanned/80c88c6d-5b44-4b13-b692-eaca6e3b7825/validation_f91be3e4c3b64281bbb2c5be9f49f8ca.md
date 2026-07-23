### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist guard — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual token payer / `msg.sender` of the pool call) and validates only `owner` (the position beneficiary). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any address that is not on the allowlist can deposit into a guarded pool by naming any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (position beneficiary)
``` [1](#0-0) 

The pool's own NatSpec documents this split explicitly: *"`msg.sender` pays but need not equal `owner` (operator pattern)."* [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but names it with an anonymous `address` placeholder, discarding it entirely. The guard is applied only to `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

`SwapAllowlistExtension.beforeSwap`, by contrast, correctly checks `sender` (the actual swapper):

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The asymmetry is the root cause: the swap guard checks the actor who executes the action; the deposit guard checks only the beneficiary.

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, salt, ...)` makes the exploit trivially accessible from the standard periphery: the caller supplies an arbitrary `owner` while `msg.sender` (the payer) is stored separately in transient context and never reaches the extension check. [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is the only on-chain mechanism pool admins have to restrict who may provide liquidity (e.g., for regulatory compliance, KYC gating, or competitive exclusion). Because the guard checks `owner` rather than `sender`, it is completely ineffective against the operator pattern:

- An address not on the allowlist can call `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)` directly, or route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedAddress, ...)`.
- The extension sees `owner = allowlistedAddress` → check passes.
- The unauthorized caller pays the tokens; the position is credited to `allowlistedAddress`.
- If the unauthorized caller and the allowlisted address are colluding (or the same entity using two wallets), the allowlisted address calls `removeLiquidity` and returns the proceeds — a complete, costless bypass of the guard.
- Even without collusion, the unauthorized caller has successfully deposited into a pool that was supposed to reject them, breaking the admin-configured access boundary.

This is an admin-boundary break: an unprivileged path bypasses a factory/pool admin-configured guard.

---

### Likelihood Explanation

- Triggering requires no special privilege — any EOA or contract can call `pool.addLiquidity` or `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with an arbitrary `owner`.
- The only prerequisite is knowing one allowlisted address, which is readable from `allowedDepositor` (public mapping) or observable from past deposits.
- The `MetricOmmPoolLiquidityAdder` periphery contract is the standard user-facing entry point and directly exposes the `owner` parameter, making the bypass path obvious.

---

### Recommendation

Change `beforeAddLiquidity` to validate `sender` (the actual depositor / token payer) instead of, or in addition to, `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    // Gate the actual depositor (token payer), not just the position beneficiary.
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the payer and the beneficiary, both `sender` and `owner` should be checked. The fix mirrors the already-correct pattern in `SwapAllowlistExtension.beforeSwap`. [4](#0-3) 

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension as beforeAddLiquidity hook.
  - Admin allowlists address A; address B is NOT allowlisted.

Attack:
  1. B calls pool.addLiquidity(
         owner = A,          // allowlisted → check passes
         salt  = 1,
         deltas = <valid bins>,
         callbackData = ...,
         extensionData = ""
     )
  2. Extension receives: sender = B (ignored), owner = A (allowlisted) → no revert.
  3. Pool mints shares under key (A, 1, bin).
  4. B's tokens are pulled via callback.
  5. A calls removeLiquidity(owner=A, salt=1, ...) and receives the tokens back.

Result: B deposited into a pool that was configured to reject B.
        The deposit allowlist guard is fully bypassed.
```

The same path is available via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, A, 1, deltas, ...)` called by B, which is the standard periphery entry point. [5](#0-4) [3](#0-2) [6](#0-5)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
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
