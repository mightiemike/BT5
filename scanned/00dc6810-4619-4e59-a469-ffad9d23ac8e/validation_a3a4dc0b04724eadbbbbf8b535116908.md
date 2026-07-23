### Title
`DepositAllowlistExtension` Checks LP Position `owner` Instead of Transaction `sender`, Allowing Any Caller to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates `owner` (the LP position recipient). Because `owner` is a free caller-supplied parameter with no pool-level restriction, any address not on the allowlist can bypass the guard by passing any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address and passes both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The extension interface therefore receives two distinct actors: `sender` = the actual depositor, `owner` = the LP position recipient.

`DepositAllowlistExtension.beforeAddLiquidity` drops `sender` entirely (unnamed first parameter) and gates only on `owner`:

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

`msg.sender` inside the extension is the pool (the caller of the hook), so `allowedDepositor[pool][owner]` is the lookup. If `owner` is any allowlisted address, the check passes regardless of who actually called `addLiquidity`.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swap initiator) and ignores `recipient`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

The asymmetry confirms the deposit extension checks the wrong actor.

---

### Impact Explanation

The deposit allowlist guard is completely ineffective. Any address not on the allowlist can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` directly (bypassing the router). The extension sees `owner = allowlistedAddress` (allowlisted), passes the check, and the attacker's tokens are pulled via the modify-liquidity callback into the pool. LP shares are credited to `allowlistedAddress`, but the attacker has successfully deposited into a permissioned pool without authorization.

Consequences:
- Pool admin's intent to restrict depositors (e.g., KYC, institutional-only, compliance) is fully defeated.
- Unauthorized parties can manipulate bin depth and pool state.
- Tokens from untrusted sources enter the pool, potentially violating invariants the allowlist was designed to enforce.

---

### Likelihood Explanation

Exploitation requires only a direct call to `pool.addLiquidity` with any allowlisted address as `owner`. No special privileges, flash loans, or complex setup are needed. The pool address and allowlisted addresses are public on-chain. Any actor who is blocked by the allowlist can trivially bypass it.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP position recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

---

### Proof of Concept

Setup:
- Pool deployed with `DepositAllowlistExtension` in `beforeAddLiquidity` order.
- `allowedDepositor[pool][alice] = true` (alice is allowlisted).
- `allowedDepositor[pool][attacker] = false` (attacker is NOT allowlisted).

Attack:
```solidity
// Attacker calls pool directly, setting owner = alice (allowlisted)
pool.addLiquidity(
    alice,          // owner — allowlisted, passes the guard
    salt,
    deltas,
    callbackData,   // attacker implements the callback, pays tokens
    extensionData
);
// Extension checks allowedDepositor[pool][alice] == true → passes
// Attacker's tokens are pulled; LP shares credited to alice
// Allowlist guard completely bypassed
```

The pool's `addLiquidity` passes `msg.sender` (attacker) as `sender` and `alice` as `owner` to the hook. [4](#0-3) 

The extension ignores `sender` (attacker) and checks only `owner` (alice). [5](#0-4) 

The check passes. The attacker has deposited into a permissioned pool without being on the allowlist.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-195)
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
