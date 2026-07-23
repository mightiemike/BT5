### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unauthorized caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates on the `owner` parameter (the position-owner address supplied by the caller) rather than the `sender` parameter (the actual `msg.sender` of `addLiquidity`). Because `owner` is a free caller-controlled argument, any address not on the allowlist can bypass the guard by passing any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with both the real caller and the requested position owner:

```solidity
// MetricOmmPool.sol – addLiquidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first argument, unnamed) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller) and ignores `recipient`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The asymmetry is the root cause. Because `owner` is a free parameter supplied by the caller, any address can pass the allowlist check by setting `owner` to any address that the pool admin has already allowlisted.

---

### Impact Explanation

An unauthorized caller (not on the allowlist) calls:

```
pool.addLiquidity(owner = <any_allowlisted_address>, salt, deltas, callbackData, extensionData)
```

The extension evaluates `allowedDepositor[pool][allowlisted_address]` → `true` → no revert. The pool then executes `LiquidityLib.addLiquidity` with `owner = allowlisted_address`, calls back on `msg.sender` (the attacker) to collect tokens, and mints the position to `allowlisted_address`. The attacker's tokens enter the pool as LP principal; the position is owned by the allowlisted address.

Concrete consequences:
- **Admin-boundary break**: the pool admin's deposit allowlist — the primary mechanism to restrict who may add liquidity to a private or permissioned pool — is fully bypassed by any unprivileged address.
- **Pool state manipulation**: the attacker can add liquidity to arbitrary bins, altering the pool's liquidity distribution and affecting which bins are consumed during swaps, without the pool admin's consent.
- **Griefing of allowlisted LPs**: positions are created for allowlisted addresses without their knowledge or consent, potentially interfering with their strategies.

---

### Likelihood Explanation

Exploitation requires no special role, no privileged key, and no complex setup. Any EOA or contract can call `addLiquidity` directly on the pool with a known allowlisted address as `owner`. The allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events. Likelihood is high whenever a pool is deployed with `DepositAllowlistExtension` configured.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, mirroring the pattern used in `SwapAllowlistExtension`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [3](#0-2) 

---

### Proof of Concept

1. Pool `P` is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook. Only address `A` is allowlisted via `setAllowedToDeposit(P, A, true)`.
2. Attacker `B` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner      = A,          // allowlisted — passes the guard
       salt       = 0,
       deltas     = <any valid deltas>,
       callbackData = <B's callback data>,
       extensionData = ""
   );
   ```
3. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[P][A]` → `true` → no revert.
4. `LiquidityLib.addLiquidity` mints the position to `A` and calls back on `B` to collect tokens.
5. `B` provides tokens; the position is created for `A` with `B`'s capital.
6. The deposit allowlist has been bypassed: `B` deposited into a pool it was explicitly excluded from.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
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
