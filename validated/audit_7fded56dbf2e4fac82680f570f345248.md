### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Callers to Bypass the Deposit Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (the LP position recipient) rather than the `sender` parameter (the actual `msg.sender` of `addLiquidity`). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unprivileged address can call `addLiquidity(owner = allowedAddress, ...)`, pass the allowlist check, and add liquidity to a permissioned pool — directly analogous to Ammplify M-3's unfettered `recipient` parameter.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes both `msg.sender` (as `sender`) and the caller-supplied `owner` to the `_beforeAddLiquidity` hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but silently discards `sender` (unnamed first parameter) and gates only on `owner`:

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

`addLiquidity` has no `msg.sender == owner` guard (unlike `removeLiquidity`, which does enforce `msg.sender == owner`): [3](#0-2) 

**Attack path:**

1. Pool admin configures `DepositAllowlistExtension` with a restricted allowlist (e.g., KYC'd addresses only). `allowedDepositor[pool][alice] = true`.
2. Attacker (not on allowlist) calls `pool.addLiquidity(owner = alice, salt = X, deltas = ..., callbackData = ..., extensionData = ...)`.
3. `beforeAddLiquidity` checks `allowedDepositor[pool][alice]` → `true`. The gate passes.
4. `LiquidityLib.addLiquidity` credits LP shares to `alice` under `salt = X`. The attacker's callback provides the tokens.
5. The attacker has successfully added liquidity to a permissioned pool without being on the allowlist.

The attacker can repeat this to:
- Manipulate bin state (shift `curBinIdx`, `curPosInBin`) in a restricted pool without authorization.
- Pollute `alice`'s LP position with unwanted shares in arbitrary bins (she must call `removeLiquidity` herself to clean up, since `removeLiquidity` enforces `msg.sender == owner`).
- Interact with the pool's extension state (e.g., update `OracleValueStopLossExtension` watermarks via the `afterAddLiquidity` hook) from an unprivileged address.

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism for permissioned pools to restrict who can add liquidity. Its bypass means:

- **Broken access control**: Any address can add liquidity to a pool the admin intended to be private/KYC-gated.
- **Unwanted LP positions**: An attacker can force LP shares onto any allowlisted address without their consent, requiring the victim to actively remove them.
- **Pool state manipulation**: Bin distribution and extension watermarks can be altered by unprivileged actors, potentially affecting all LPs in the pool.

The `SwapAllowlistExtension` correctly checks `sender` (the actual caller), making the inconsistency with `DepositAllowlistExtension` a clear implementation error rather than a design choice. [4](#0-3) 

---

### Likelihood Explanation

- No special privileges required — any EOA or contract can call `addLiquidity` with an arbitrary `owner`.
- The allowlisted address to use as `owner` is discoverable on-chain (emitted via `AllowedToDepositSet` events).
- The attacker only needs to fund the callback; the cost is the token amount deposited (which they can recover indirectly if they control the `owner` address, or simply accept as a cost of manipulation).

---

### Recommendation

Change `beforeAddLiquidity` to validate `sender` (the actual caller) instead of `owner`:

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

This mirrors the correct pattern already used in `SwapAllowlistExtension`, which gates on `sender`. [2](#0-1) 

---

### Proof of Concept

```solidity
// Assume pool has DepositAllowlistExtension configured:
//   allowedDepositor[pool][alice] = true
//   attacker is NOT on the allowlist

// Attacker bypasses the allowlist:
pool.addLiquidity(
    alice,          // owner — passes the allowlist check
    uint80(42),     // salt
    deltas,         // liquidity to add
    callbackData,   // attacker's callback provides tokens
    extensionData
);
// Result: LP shares credited to alice, attacker interacted with a permissioned pool
// alice must call removeLiquidity herself to reclaim the tokens
```

The `beforeAddLiquidity` check at line 38 evaluates `allowedDepositor[pool][alice]` (true), not `allowedDepositor[pool][attacker]` (false), so the revert is never reached. [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
