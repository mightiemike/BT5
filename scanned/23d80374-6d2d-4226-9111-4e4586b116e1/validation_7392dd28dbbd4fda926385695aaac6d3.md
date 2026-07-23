### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates the `owner` argument (the LP-position recipient). Because `addLiquidity` lets any caller specify any `owner`, an address that is not on the allowlist can deposit into a restricted pool by naming an allowlisted address as `owner`, completely bypassing the access gate.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*owner*/, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling` forwards both to the extension verbatim:

```
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but discards `sender` (the `address,` wildcard) and checks only `owner`:

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

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper), confirming the asymmetry is unintentional:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [4](#0-3) 

Because `addLiquidity` imposes no restriction on who may supply an arbitrary `owner` address, any caller can name an allowlisted address as `owner` and the guard passes unconditionally.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may add liquidity to a private or permissioned pool. With this bug the guard is entirely inoperative: any unprivileged address can inject liquidity into the pool, alter bin balances, affect fee accrual, and change the pool's liquidity profile in ways the admin explicitly prohibited. The LP-position shares are minted to the named `owner` (not the attacker), so the attacker's tokens enter the pool permanently — but the pool's invariant that only allowlisted parties may deposit is broken, constituting a broken core pool functionality and an admin-boundary break reachable by any unprivileged caller.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with any allowlisted address as `owner`. No special role, flash loan, or oracle manipulation is needed. Any address that can observe the allowlist (public mappings) can exploit this immediately.

---

### Recommendation

Replace the discarded first parameter with a named `sender` variable and check it instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached and `allowAllDepositors[pool] = false`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. `bob` (not allowlisted) calls `pool.addLiquidity(alice /*owner*/, salt, deltas, callbackData, extensionData)`.
4. The pool calls `DepositAllowlistExtension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
5. The extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `bob`'s callback is invoked; `bob` transfers tokens into the pool; `alice` receives LP shares.
7. `bob` has successfully deposited into a pool he was explicitly barred from, bypassing the allowlist entirely. [3](#0-2) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-99)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
