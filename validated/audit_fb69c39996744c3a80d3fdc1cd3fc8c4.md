### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and gates on `owner` (the LP-position recipient) instead. Because the allowlist is keyed by *depositor* (the actual caller), any address can bypass the restriction by naming an allowlisted address as `owner`. This is the direct analog of the external bug: the wrong address is used for the critical check, making the guard permanently ineffective against the actor it was designed to block.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (LP-position recipient)
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to every configured extension:

```
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension`, the first parameter (`sender`) is unnamed and discarded; the check is performed on `owner`:

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

The admin-facing setter names its parameter `depositor`, confirming the intended subject of the check is the caller, not the position recipient:

```solidity
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
``` [4](#0-3) 

`SwapAllowlistExtension` correctly checks `sender` (the actual swapper) and ignores `recipient`, making the inconsistency in `DepositAllowlistExtension` clear:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [5](#0-4) 

---

### Impact Explanation

**Bypass (primary):** Any address not on the allowlist calls `addLiquidity(owner = allowlisted_address, ...)`. The extension evaluates `allowedDepositor[pool][allowlisted_address]` → `true` and passes. The unauthorized caller provides the tokens via callback; the LP position is minted to `allowlisted_address`. The deposit restriction is completely defeated for every pool that deploys this extension.

**False positive (secondary):** A legitimately allowlisted address that calls `addLiquidity` with `owner` set to a smart-contract wallet, vault, or any address not separately allowlisted will be incorrectly blocked, breaking the liquidity-add flow for valid users.

Both effects undermine the pool admin's ability to control who can add liquidity — the core purpose of the extension.

---

### Likelihood Explanation

Exploitation requires no special privilege. Any EOA or contract can call `addLiquidity` on a pool with this extension active, supply any allowlisted address as `owner`, and succeed. The allowlisted address is publicly readable via `allowedDepositor` or `isAllowedToDeposit`. No flash loan, callback trick, or elevated role is needed.

---

### Recommendation

Name and use `sender` in `beforeAddLiquidity`, mirroring the pattern in `SwapAllowlistExtension`:

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

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only address `A`:
   `setAllowedToDeposit(pool, A, true)`
2. Unauthorized address `B` (not allowlisted) calls:
   `pool.addLiquidity(owner = A, salt, deltas, callbackData, extensionData)`
3. Pool calls `extension.beforeAddLiquidity(sender=B, owner=A, ...)`.
4. Extension evaluates `allowedDepositor[pool][A]` → `true` → no revert.
5. `B` provides tokens via the swap callback; the LP position is minted to `A`.
6. `B` has successfully added liquidity to a restricted pool without being on the allowlist.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-39)
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
```
