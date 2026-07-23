### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any unprivileged address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and checks `owner` (the position recipient) instead. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` address with no allowlist validation, any non-allowlisted caller can deposit into a gated pool by nominating any allowlisted address as `owner`.

---

### Finding Description

`ExtensionCalling._beforeAddLiquidity` forwards two distinct actors to every extension:

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*owner*/, ...)
``` [1](#0-0) 

`sender` is the address that actually called `addLiquidity` (the real depositor or router). `owner` is the position-recipient parameter supplied by the caller.

`SwapAllowlistExtension` correctly gates on `sender`:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [2](#0-1) 

`DepositAllowlistExtension` does the opposite — it silently discards `sender` (first parameter is unnamed) and checks `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

The admin-facing setter names the second argument `depositor`, confirming the intent was to gate the actual depositing address, not the position owner:

```solidity
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
``` [4](#0-3) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` with only a zero-address check:

```solidity
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);   // only checks owner != address(0)
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [5](#0-4) 

This means any non-allowlisted `msg.sender` can pass an allowlisted address as `owner`, and the extension will approve the deposit.

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism for restricting who may deposit into a gated pool (KYC/AML, private LP pools, cap enforcement). With the check on the wrong actor, the guard is completely ineffective: any address can deposit into a restricted pool by nominating any allowlisted address as the position owner. The non-allowlisted depositor's tokens enter the pool and the LP position is credited to the nominated owner. This breaks the core deposit-gating invariant the extension is designed to enforce.

---

### Likelihood Explanation

The bypass requires no special privilege. Any address can call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with a publicly known allowlisted address (e.g., the pool admin, a known LP, or any address visible on-chain) as `owner`. The allowlisted owner need not cooperate. The attack is trivially repeatable.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositing caller), consistent with how `SwapAllowlistExtension` handles `beforeSwap`:

```solidity
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate on the position owner (not the caller), the mapping name, setter parameter name, and documentation must be updated to reflect that, and the `addLiquidityExactShares` path must be re-evaluated for whether an arbitrary `owner` is acceptable.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — Alice is the only allowlisted depositor.
3. Bob (not allowlisted) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       pool,
       alice,   // owner = allowlisted address
       salt,
       deltas,
       max0, max1,
       extensionData
   );
   ```
4. Pool calls `_beforeAddLiquidity(liquidityAdder /*sender*/, alice /*owner*/, ...)`.
5. Extension checks `allowedDepositor[pool][alice]` → `true` → no revert.
6. Bob's tokens are pulled from Bob and deposited; Alice receives the LP position.
7. The deposit allowlist has been bypassed by an unprivileged actor with zero cooperation from Alice. [3](#0-2) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
