### Title
`DepositAllowlistExtension` Checks LP Position Owner Instead of Actual Depositor, Allowing Complete Allowlist Bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` receives both the actual depositor (`sender`) and the LP position recipient (`owner`) but silently ignores `sender` and only checks `owner`. Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unprivileged actor can deposit into a restricted pool by supplying an allowlisted address as `owner`, completely nullifying the guard.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address with no ownership check: [1](#0-0) 

It then forwards both `msg.sender` (as `sender`) and the caller-supplied `owner` to the extension hook: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but discards it (unnamed `address,`) and gates only on `owner`: [3](#0-2) 

Contrast this with `SwapAllowlistExtension.beforeSwap`, which correctly gates on `sender` (the actual caller): [4](#0-3) 

And contrast with `removeLiquidity`, which correctly enforces `msg.sender == owner`: [5](#0-4) 

The asymmetry is the root cause: `addLiquidity` has no caller-identity guard, and the extension checks the wrong identity.

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism for restricting who may provide tokens to a pool. Because it checks `owner` rather than `sender`, the guard is completely inoperative:

1. **Allowlist bypass**: Any non-allowlisted actor can deposit tokens into a restricted pool by naming an allowlisted address as `owner`. The pool receives tokens from an unauthorized source, violating the pool admin's access-control invariant.
2. **Forced LP position on non-consenting contracts**: An attacker can call `addLiquidity(victimContract, ...)` where `victimContract` is an allowlisted contract that holds no LP-management logic. The attacker's tokens are deposited, the LP shares are credited to `victimContract`, and because `removeLiquidity` requires `msg.sender == owner`, only `victimContract` can withdraw them. If `victimContract` lacks that call path, the deposited tokens are permanently locked in the pool — a direct loss of the attacker's principal and an unremovable LP position for the victim contract.

---

### Likelihood Explanation

Exploitation requires no special privilege. Any externally-owned account or contract can call `addLiquidity` with an arbitrary `owner`. The only prerequisite is knowing one allowlisted address, which is readable from `allowedDepositor` (a public mapping). Likelihood is **High**.

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` must gate on `sender` (the actual token provider), not `owner` (the LP recipient):

```solidity
// BEFORE (broken — checks owner, ignores sender)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}

// AFTER (correct — checks sender, the actual depositor)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Optionally, `MetricOmmPool.addLiquidity` could also enforce `msg.sender == owner` (as `removeLiquidity` does) unless a deliberate "deposit on behalf of" use-case is intended and documented.

---

### Proof of Concept

**Setup:**
- Pool `P` has `DepositAllowlistExtension` configured.
- `allowedDepositor[P][alice] = true`; Bob is **not** allowlisted.

**Attack:**
1. Bob calls `P.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
2. Pool calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
3. Extension evaluates `allowedDepositor[P][alice]` → `true` → no revert.
4. Pool calls back to Bob (`IMetricOmmSwapCallback(bob).metricOmmSwapCallback(...)`) to pull tokens; Bob transfers tokens to the pool.
5. LP shares are minted and credited to `alice`.

**Result A (allowlist bypass):** Bob, a non-allowlisted actor, has successfully deposited into a restricted pool. The pool admin's access control is completely defeated.

**Result B (forced lock):** If `alice` is replaced with an allowlisted contract `C` that has no `removeLiquidity` call path, Bob's tokens are permanently locked in the pool as LP shares owned by `C`, which can never be redeemed.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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
