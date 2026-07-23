### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unauthorized Depositor to Bypass the Allowlist Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller who provides tokens via callback) and instead validates the `owner` argument (the position-credit recipient). Any non-allowlisted entity can bypass the guard by supplying any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (position recipient, caller-supplied)
``` [1](#0-0) 

Inside `DepositAllowlistExtension.beforeAddLiquidity` the first positional parameter (`sender`) is unnamed and discarded; the guard reads only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

`owner` is a **caller-supplied** argument with no constraint that it equals `msg.sender`. The entity that actually provides tokens is `sender` (`msg.sender` of the pool call), whose identity is never examined.

The sibling `SwapAllowlistExtension.beforeSwap` demonstrates the correct pattern — it checks `sender` (the actual swapper), not the recipient:

```solidity
function beforeSwap(address sender, address, ...)
    ...
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

`DepositAllowlistExtension` is inconsistent with this established pattern and with its own NatSpec ("Gates `addLiquidity` by depositor address"). [4](#0-3) 

---

### Impact Explanation

Any non-allowlisted address can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)`. The guard checks `allowedDepositor[pool][allowlistedAddress]` → passes. The non-allowlisted caller provides tokens via the modify-liquidity callback and injects liquidity into arbitrary bins. The pool admin's deposit restriction — the sole mechanism for controlling who can alter pool liquidity composition — is rendered ineffective. An attacker can:

1. Inject liquidity into targeted bins to shift the pool's marginal price or bin cursor.
2. Exploit the resulting price distortion in a subsequent swap (if swap access is open or the attacker is allowlisted for swaps).
3. Remove the position (credited to the allowlisted `owner`) is not possible for the attacker, but the pool state manipulation itself is the harm vector.

This breaks the "admin-boundary" invariant: an unprivileged path bypasses a pool-admin-configured access control, with potential for pool state manipulation and indirect fund impact on existing LPs through price distortion.

---

### Likelihood Explanation

Exploitation requires only knowing one allowlisted address (observable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` reads) and calling `addLiquidity` with that address as `owner`. No special privileges, flash loans, or oracle manipulation are needed. Likelihood is **high** whenever the pool has at least one allowlisted depositor.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// Before (wrong):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (correct):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

Also update `setAllowedToDeposit` / `isAllowedToDeposit` documentation and any off-chain tooling that manages the allowlist to reflect that the key is the **caller** of `addLiquidity`, not the position owner.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** allowlisted.
2. Bob calls `pool.addLiquidity(alice, 0, deltas, callbackData, "")`.
3. Pool calls `extension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
4. Guard evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. Pool calls `IMetricOmmModifyLiquidityCallback(bob).metricOmmModifyLiquidityCallback(...)` — Bob transfers tokens into the pool.
6. Liquidity is minted into the targeted bins; position is credited to Alice.
7. Bob has injected liquidity into the restricted pool without being on the allowlist, bypassing the guard entirely. [2](#0-1) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-12)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
