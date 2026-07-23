### Title
DepositAllowlistExtension Guards LP Share Recipient (`owner`) Instead of Token Provider (`sender`), Allowing Unauthorized Liquidity Injection — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual token provider / pool caller) and enforces the allowlist only against `owner` (the LP-share recipient). Any address can bypass the deposit gate by calling `addLiquidity` with `owner` set to an allowlisted address, providing tokens themselves via the swap callback, and forcing LP exposure onto the allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the before-hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The extension receives `(sender, owner, …)` but the `sender` argument is unnamed and never read:

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

The admin-facing setter names its second argument `depositor`, making the design intent unambiguous — the guard is supposed to restrict who provides tokens, not who receives shares:

```solidity
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
``` [3](#0-2) 

Because `addLiquidity` uses a pull-callback pattern, `msg.sender` (the pool's caller) is the entity that actually transfers tokens into the pool. `owner` is only the LP-share recipient. By checking `owner` instead of `sender`, the guard is applied to the wrong actor.

---

### Impact Explanation

**Unauthorized liquidity injection.** An unprivileged caller Bob sets `owner = Alice` (an allowlisted address). The allowlist check passes (`allowedDepositor[pool][Alice] == true`). Bob's callback transfers his tokens into the pool; Alice receives LP shares she never requested.

**Forced LP exposure / fund loss for Alice.** Alice now holds a position she did not create. If the pool's oracle price moves adversely before Alice notices and removes liquidity, Alice suffers a real token loss. She cannot prevent the position from being opened on her behalf.

**Allowlist invariant broken.** The pool admin configured the extension specifically to restrict which addresses may deposit. That invariant is fully bypassed by any unprivileged address at zero cost beyond the tokens they choose to inject.

---

### Likelihood Explanation

The bypass requires only a standard `addLiquidity` call with a known allowlisted address as `owner`. No privileged access, flash loan, or exotic token behavior is needed. Any allowlisted address is publicly discoverable on-chain via `AllowedToDepositSet` events. The attack is therefore trivially executable by any on-chain actor.

---

### Recommendation

Replace the unnamed first parameter with `sender` and enforce the allowlist against it:

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

If the intent is to allow any caller to deposit on behalf of an allowlisted owner (e.g., a router acting for Alice), then both `sender` and `owner` should be checked, or the interface documentation must explicitly state that `owner` is the gated actor and the admin UI must be updated accordingly.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, Alice, true)`. Bob is not allowlisted.
2. Bob constructs a `LiquidityDelta` targeting any bin and calls:
   ```solidity
   pool.addLiquidity(
       owner        = Alice,   // allowlisted — passes the guard
       salt         = 0,
       deltas       = <Bob's chosen bins/shares>,
       callbackData = <Bob's token approval data>,
       extensionData = ""
   );
   ```
3. `_beforeAddLiquidity(msg.sender=Bob, owner=Alice, …)` is dispatched. The extension checks `allowedDepositor[pool][Alice]` → `true`. No revert.
4. `LiquidityLib.addLiquidity` mints shares to Alice and calls back Bob to pull tokens. Bob's tokens enter the pool.
5. Alice now holds an LP position she never created. Bob has bypassed the deposit allowlist entirely. [2](#0-1) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
