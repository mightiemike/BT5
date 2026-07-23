### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual `msg.sender` of `addLiquidity`) and only checks `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` explicitly permits `msg.sender ≠ owner` via the operator pattern, any unprivileged address can call `addLiquidity` directly with `owner` set to an allowlisted address, paying tokens through the callback while the allowlist check passes on the allowlisted owner — never touching the real caller.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension dispatcher as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both `sender` and `owner` to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first (unnamed, discarded) parameter and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

The pool's own NatSpec documents the operator pattern that makes this exploitable:

> `msg.sender` pays but need not equal `owner` (operator pattern). [4](#0-3) 

Because `sender` is never checked, any address can call `pool.addLiquidity(owner = allowlisted_alice, ...)` directly. The extension evaluates `allowedDepositor[pool][alice]` → `true` and returns the success selector. The actual caller (Bob, not allowlisted) then pays tokens through the `IMetricOmmModifyLiquidityCallback` and Alice receives LP shares — the allowlist is fully bypassed.

No existing guard in `addLiquidity` checks `msg.sender` independently of the extension:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
(amount0Added, amount1Added) = LiquidityLib.addLiquidity(...);
_afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
``` [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is the pool admin's sole mechanism to curate who participates in the pool. Bypassing it allows an unprivileged caller to:

1. **Inject tokens into a curated pool** without being allowlisted — breaking the admin-configured boundary.
2. **Force an allowlisted address to receive LP shares it did not request** — griefing that address with unwanted pool exposure and potential impermanent loss.
3. **Manipulate pool cursor and bin balances** in a pool that was supposed to be restricted to vetted participants, potentially harming existing allowlisted LPs.

This is a broken core pool functionality / admin-boundary break with direct LP asset impact.

---

### Likelihood Explanation

**High.** The exploit requires no special privileges, no flash loans, and no complex setup. Any EOA or contract can call `pool.addLiquidity` directly with `owner` set to any known allowlisted address. The allowlisted address does not need to cooperate. The attacker only needs to fund the callback payment.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual caller) rather than `owner` (the position recipient), consistent with the extension's stated purpose of gating the depositor:

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

If the intent is instead to gate the position recipient (owner), the extension name, NatSpec, and `setAllowedToDeposit` semantics should be updated to reflect that, and the operator-pattern bypass documented explicitly. Pool admins using this extension to restrict participation must be aware that checking `owner` does not prevent an unprivileged `sender` from paying into the pool.

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension as EXTENSION_1
  - Alice (0xAlice) is allowlisted: allowedDepositor[pool][0xAlice] = true
  - Bob (0xBob) is NOT allowlisted

Attack:
  1. Bob calls pool.addLiquidity(
         owner    = 0xAlice,   // allowlisted address
         salt     = 0,
         deltas   = { binIdxs: [0], shares: [1000] },
         callbackData = abi.encode(...),
         extensionData = ""
     )

  2. Pool calls _beforeAddLiquidity(sender=0xBob, owner=0xAlice, ...)
     → DepositAllowlistExtension checks allowedDepositor[pool][0xAlice] → true → passes

  3. LiquidityLib.addLiquidity mints shares to position key (0xAlice, 0)

  4. Pool calls IMetricOmmModifyLiquidityCallback(0xBob).metricOmmModifyLiquidityCallback(...)
     → Bob pays token0/token1 amounts

Result:
  - Bob (not allowlisted) has paid tokens into the curated pool ✓
  - Alice receives LP shares she did not request ✓
  - Allowlist is bypassed with zero special privileges ✓
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
```
