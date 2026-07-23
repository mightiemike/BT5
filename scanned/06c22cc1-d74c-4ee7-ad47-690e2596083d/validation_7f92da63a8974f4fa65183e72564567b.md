Looking at the `DepositAllowlistExtension` and how `addLiquidity` passes parameters to it, there is a clear analog.

**Key observation**: `DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` parameter (user-supplied, arbitrary address) instead of the `sender` parameter (actual `msg.sender` of the pool call). Compare this directly with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender`.

---

### Title
`DepositAllowlistExtension` checks user-supplied `owner` instead of actual caller `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking whether the `owner` argument is allowlisted. Because `owner` is a free caller-supplied parameter in `MetricOmmPool.addLiquidity`, any unprivileged address can bypass the allowlist by passing an allowlisted address as `owner`. The actual depositor (`sender`) is silently ignored.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and passes both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values verbatim: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender` — the actual caller) is unnamed and discarded. The allowlist check is performed only against `owner`: [3](#0-2) 

Contrast this with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller): [4](#0-3) 

The inconsistency is the root cause. Because `owner` is freely chosen by the caller, any address can call:

```solidity
pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)
```

The extension sees `owner = allowlisted_address`, passes the check, and the pool proceeds. The unauthorized caller supplies the tokens via the swap callback; the resulting LP position is recorded under `allowlisted_address`. The pool admin's deposit restriction is entirely bypassed.

---

### Impact Explanation

A pool configured with `DepositAllowlistExtension` to restrict liquidity providers receives unauthorized liquidity from any address willing to supply tokens. This:

1. **Breaks the deposit allowlist invariant** — the core purpose of the extension is defeated.
2. **Dilutes existing allowlisted LPs** — unauthorized liquidity competes for the same fee stream, reducing the fee share of legitimate LPs.
3. **Corrupts pool composition** — if the allowlist exists to maintain a specific LP set (e.g., a private or curated pool), the pool's economic properties are altered without admin consent.

The position is owned by the allowlisted address (who can withdraw it), but the pool has already received and accounted for the unauthorized tokens in `binTotals`, affecting all subsequent swaps and fee distributions until the position is removed. [5](#0-4) 

---

### Likelihood Explanation

- Any address can trigger this with a single `addLiquidity` call.
- No privileged access, no special setup, and no front-running is required.
- The attacker only needs to know one allowlisted address (publicly readable from `allowedDepositor` mapping or event logs).
- The attacker loses the deposited tokens (position owned by the allowlisted address), but this is a low barrier for a griefing or dilution attack.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, matching the pattern used by `SwapAllowlistExtension`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

---

### Proof of Concept

```solidity
// Pool P has DepositAllowlistExtension configured.
// Alice (0xAlice) is the only allowlisted depositor.
// Bob (0xBob) is NOT allowlisted.

// Bob calls addLiquidity with owner = Alice's address:
pool.addLiquidity(
    0xAlice,          // owner — allowlisted, passes the check
    0,                // salt
    deltas,           // liquidity to add
    callbackData,     // Bob's contract supplies tokens here
    extensionData
);

// Result:
// - Extension checks allowedDepositor[pool][0xAlice] → true → no revert
// - Bob's callback transfers Bob's tokens into the pool
// - LP position recorded under Alice's address
// - Pool now holds unauthorized liquidity; existing LPs' fee share is diluted
// - Alice can withdraw the position, but the pool has already been affected
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
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
