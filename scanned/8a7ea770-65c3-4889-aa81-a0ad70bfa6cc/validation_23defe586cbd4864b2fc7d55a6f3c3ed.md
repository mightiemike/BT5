### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (the LP-position recipient) against the per-pool allowlist instead of the `sender` parameter (the actual caller who provides tokens). Because `addLiquidity` imposes no constraint that `msg.sender == owner`, any address not on the allowlist can deposit tokens into the pool by naming an allowlisted address as `owner`, bypassing the guard entirely.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address parameters: the unnamed first argument (`sender` — the `msg.sender` of the original `addLiquidity` call, i.e., the token provider) and `owner` (the address that will hold the resulting LP shares). [1](#0-0) 

The check performed is:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
```

`owner` is the LP-position recipient, **not** the address supplying tokens. The pool passes these two values distinctly: [2](#0-1) 

`msg.sender` (the actual depositor) is forwarded as `sender`; `owner` is a separate caller-supplied argument. Crucially, `addLiquidity` has **no** `msg.sender == owner` guard — that constraint exists only in `removeLiquidity`: [3](#0-2) 

The `ExtensionCalling._beforeAddLiquidity` wiring confirms `sender` and `owner` are independent: [4](#0-3) 

The parallel `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual swap initiator), not on `recipient`: [5](#0-4) 

This inconsistency confirms the deposit extension checks the wrong address.

---

### Impact Explanation

The `DepositAllowlistExtension` is described as "Gates `addLiquidity` by depositor address, per pool." When a pool admin enables this guard — for example to restrict liquidity provision to KYC'd counterparties, to prevent a specific address from accumulating LP shares, or to enforce regulatory controls — the guard is silently ineffective. Any address can deposit tokens and receive LP shares (via a nominated allowlisted `owner`), or can deposit tokens on behalf of an allowlisted address without that address's active participation. The pool admin's intended access boundary is fully bypassed, and the pool's LP composition is no longer under the admin's control.

---

### Likelihood Explanation

The bypass requires only that the attacker knows one allowlisted address (trivially discoverable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` reads) and is willing to assign the resulting LP shares to that address. No privileged access, flash loan, or oracle manipulation is needed. Any unprivileged external caller can trigger this path on every pool that has deployed `DepositAllowlistExtension` without `allowAllDepositors` set.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring the pattern used in `SwapAllowlistExtension`:

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
``` [1](#0-0) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only address **A**:
   ```
   setAllowedToDeposit(pool, A, true)
   ```
2. Address **B** (not allowlisted) constructs a call:
   ```
   pool.addLiquidity(owner = A, salt, deltas, callbackData, extensionData)
   ```
3. Pool calls `extension.beforeAddLiquidity(sender=B, owner=A, ...)`.
4. Extension evaluates `allowedDepositor[pool][A]` → `true` → **no revert**.
5. B's callback provides the tokens; A receives the LP shares.
6. B has deposited into the allowlist-gated pool without being on the allowlist.

The root cause is identical in structure to the EIP-2981 analog: a wrong input (constant `BASIS_POINTS` / wrong address `owner`) is substituted for the correct one (actual `_salePrice` / actual depositor `sender`), causing the guard to evaluate against a value it was never meant to gate.

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
