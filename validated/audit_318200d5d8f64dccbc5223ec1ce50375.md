### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual depositor who provides funds via callback) and instead validates the caller-controlled `owner` parameter against the allowlist. Because `owner` is a free argument in `MetricOmmPool.addLiquidity`, any address — including one that is explicitly not on the allowlist — can bypass the deposit gate by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from the caller and passes both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both values faithfully to the extension:

```solidity
// ExtensionCalling.sol lines 95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

Inside `DepositAllowlistExtension`, the first positional argument (`sender`) is unnamed and discarded; only `owner` is checked:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is entirely attacker-controlled. Any caller can pass any allowlisted address as `owner` and the guard passes unconditionally.

Compare with `SwapAllowlistExtension`, which correctly checks `sender` (the actual swapper), not `recipient`:

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The asymmetry confirms the deposit extension has the wrong parameter bound.

---

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to restrict who can provide liquidity to a permissioned pool. With this bug the restriction is completely ineffective:

1. **Unauthorized depositor using an allowlisted owner as a pass-through**: An address not on the allowlist calls `addLiquidity(allowlistedAddress, salt, deltas, ...)`. The extension approves because `allowedDepositor[pool][allowlistedAddress]` is `true`. The unauthorized address provides funds via the callback; the LP position is minted to `allowlistedAddress`. If `allowlistedAddress` is the attacker's own second account or a cooperating party, the attacker retains full control of the LP position and can later call `removeLiquidity` to recover funds — a complete allowlist bypass with no fund loss to the attacker.

2. **Griefing / forced LP position**: A third-party attacker can force an allowlisted address to receive an unwanted LP position in bins of the attacker's choosing, potentially distorting the pool's liquidity distribution and harming existing LPs.

Both paths break the pool admin's access-control invariant. The first path constitutes a full bypass of a configured security guard by an unprivileged actor.

---

### Likelihood Explanation

Exploitation requires only a single direct call to `pool.addLiquidity` with `owner` set to any address that appears in `allowedDepositor[pool]`. No special privileges, flash loans, or complex setup are needed. The allowlist is publicly readable, so any attacker can enumerate valid `owner` values. Likelihood is **High**.

---

### Recommendation

Bind the check to `sender` (the actual depositor) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

```
Setup
─────
1. Pool deployed with DepositAllowlistExtension as a beforeAddLiquidity hook.
2. Pool admin calls setAllowedToDeposit(pool, alice, true).
   → allowedDepositor[pool][alice] = true
3. bob is NOT on the allowlist.

Attack
──────
4. bob calls pool.addLiquidity(
       owner        = alice,   // allowlisted address
       salt         = 0,
       deltas       = <bins bob wants to fill>,
       callbackData = "",
       extensionData= ""
   )

5. Pool calls _beforeAddLiquidity(msg.sender=bob, owner=alice, ...).

6. Extension receives (sender=bob [discarded], owner=alice).
   Check: allowedDepositor[pool][alice] == true  → PASSES.

7. LiquidityLib.addLiquidity runs; bob's metricOmmSwapCallback is invoked;
   bob transfers tokens into the pool.

8. LP shares are minted to alice (owner).

9. If alice == bob's second account (or cooperating address):
   alice calls pool.removeLiquidity(alice, 0, deltas, "") → recovers funds.
   Net result: bob deposited into a pool he was explicitly barred from.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
