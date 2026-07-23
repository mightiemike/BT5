### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Callers to Bypass the Deposit Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently ignores the `sender` parameter (the actual caller) and instead validates `owner` (the LP-position beneficiary). The parallel `SwapAllowlistExtension.beforeSwap` correctly checks `sender`. The mismatch lets any unprivileged address call `addLiquidity` on a restricted pool by nominating an allowlisted address as `owner`, bypassing the guard entirely.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as the position owner to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional parameter (`sender`) is **unnamed and discarded**; only `owner` is checked: [3](#0-2) 

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` and ignores `recipient`: [4](#0-3) 

Because `owner` is a **caller-controlled parameter** with no on-chain binding to `msg.sender`, any address can pass the guard by supplying an allowlisted address as `owner`. The actual depositor (`sender`) is never validated.

A secondary consequence: a pool admin who allowlists the `MetricOmmPoolLiquidityAdder` address (by analogy with how the router is allowlisted for swaps) will find that **no deposit through the adder ever succeeds**, because the check is on `owner` (the user's address), not `sender` (the adder's address). This silently breaks the intended deposit flow for all users of the adder. [5](#0-4) 

---

### Impact Explanation

1. **Guard bypass**: Any unprivileged address can call `addLiquidity` on a pool whose admin intended to restrict deposits to a KYC/allowlisted set. The caller pays the tokens; the LP position accrues to the allowlisted `owner`. The attacker can therefore manipulate bin balances, the cursor position (`curBinIdx`, `curPosInBin`), and `binTotals` — all of which directly affect swap pricing and LP redemption values for existing LPs.

2. **Adder misconfiguration breaks deposits**: If the admin allowlists `MetricOmmPoolLiquidityAdder` (the natural analogue of allowlisting the router for swaps), every `addLiquidityExactShares` / `addLiquidityWeighted` call reverts with `NotAllowedToDeposit` because the adder's address is never the `owner`. This renders the deposit path unusable — a broken core liquidity flow.

---

### Likelihood Explanation

- The bypass requires no privilege: any EOA or contract can call `pool.addLiquidity(allowlistedAddress, ...)`.
- The allowlisted address set is observable on-chain via `allowedDepositor` mapping.
- The adder misconfiguration is a natural mistake given the symmetric design of `SwapAllowlistExtension`.

---

### Recommendation

Mirror the `SwapAllowlistExtension` pattern: check `sender`, not `owner`.

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantic is to gate the **position owner** (not the caller), the extension name, NatSpec, and admin-facing documentation must be updated to reflect that, and the adder integration must be re-evaluated.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][Alice] = true
  Bob is NOT allowlisted

Attack:
  Bob calls pool.addLiquidity(
      owner    = Alice,   // allowlisted → check passes
      salt     = 0,
      deltas   = <any valid delta>,
      ...
  )

Result:
  • beforeAddLiquidity receives sender=Bob (ignored), owner=Alice (passes check)
  • Bob pays token0/token1 via metricOmmModifyLiquidityCallback
  • Alice receives LP shares in the restricted pool
  • Bob has successfully interacted with a pool he is not authorized to touch,
    altering bin balances and cursor state without being on the allowlist
``` [3](#0-2) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
