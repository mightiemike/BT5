### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any actor to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor address** (the actual caller). However, its `beforeAddLiquidity` hook silently discards the `sender` argument and instead checks `owner` (the position owner). Any unprivileged actor can bypass the allowlist by calling `addLiquidity` with `owner` set to any allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

So the first argument is the **actual caller** (`sender`) and the second is the **position owner** (`owner`). Inside `DepositAllowlistExtension`, the `sender` parameter is unnamed and discarded; the allowlist lookup is performed against `owner`:

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

The admin-facing setter and the view helper both use the word **depositor**, confirming the intended subject is the caller, not the owner:

```solidity
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
``` [3](#0-2) 

The inconsistency is confirmed by comparing with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller of `swap`):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The deposit extension applies the same pattern to the wrong field.

---

### Impact Explanation

The deposit allowlist is the primary access-control mechanism for restricted pools. With this bug, any actor — regardless of allowlist status — can add liquidity to a pool by supplying `owner = <any allowlisted address>`. The extension passes because it checks the allowlisted owner, not the unauthorized caller. The unauthorized caller pays the tokens; the allowlisted address receives the LP position. The pool admin's intended access boundary is fully broken: the guard can be bypassed by any unprivileged actor with zero special privilege.

Secondary consequences include:
- Unauthorized actors can alter bin composition and pool depth in a pool the admin intended to keep restricted.
- An allowlisted address can be forced into holding an LP position it never requested (requiring gas to remove).
- Any compliance or operational reason for restricting deposits is nullified.

---

### Likelihood Explanation

The bypass requires no special privilege, no flash loan, and no oracle manipulation. Any caller can set `owner` to any address they know is allowlisted (e.g., the pool admin itself, which is publicly readable via `IMetricOmmPoolFactory.poolAdmin`). The attack is trivially executable on every pool that deploys `DepositAllowlistExtension` without `allowAllDepositors` set.

---

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`. [2](#0-1) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` as a `beforeAddLiquidity` extension.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowlisted.
3. Bob (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner = alice,   // allowlisted address
       salt,
       deltas,
       callbackData,
       extensionData
   );
   ```
4. `MetricOmmPool` calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
5. `DepositAllowlistExtension.beforeAddLiquidity` receives `sender = bob` (discarded) and `owner = alice`.
6. Check: `allowedDepositor[pool][alice]` → `true` → hook returns success selector.
7. Bob's tokens are pulled via callback; Alice receives the LP position.
8. The deposit allowlist is fully bypassed by an unprivileged actor. [2](#0-1) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-40)
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
```
