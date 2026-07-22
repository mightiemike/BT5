### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the actual caller) and enforces the allowlist only against the caller-supplied `owner` parameter. Because `owner` is freely chosen by the caller in `MetricOmmPool.addLiquidity`, any address—regardless of allowlist status—can deposit into a restricted pool by naming an already-allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and passes both `msg.sender` (the real caller) and `owner` into the extension hook chain:

```solidity
// MetricOmmPool.sol – addLiquidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both values faithfully:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` then discards `sender` entirely and gates only on `owner`:

```solidity
function beforeAddLiquidity(address /*sender*/, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a free parameter supplied by the caller. Any address can pass an allowlisted address as `owner`, causing the guard to approve the deposit even though the actual depositing caller is not on the allowlist.

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller) and ignores `recipient`:

```solidity
function beforeSwap(address sender, address /*recipient*/, ...)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry between the two extensions confirms this is an implementation error, not a design choice.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., KYC gating, institutional-only pools, regulatory compliance). With this bypass:

- Any unprivileged address can inject liquidity into a pool that is supposed to be restricted.
- The pool admin's access-control boundary is broken by an unprivileged path, satisfying the "admin-boundary break" impact criterion.
- Tokens provided by the unauthorized caller are permanently locked in the pool under the named `owner`'s position (only `owner` can call `removeLiquidity`), so the unauthorized caller cannot recover them—but the pool's liquidity composition is altered without the admin's consent, and the allowlist invariant is violated.

---

### Likelihood Explanation

Exploitation requires only a single `addLiquidity` call with any allowlisted address as `owner`. No special privileges, flash loans, or multi-step setup are needed. Any address that can observe the allowlist (public mapping) can execute the bypass immediately.

---

### Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate on the LP position owner (not the caller), the extension's documentation and the pool admin's mental model must be updated accordingly—but the current naming (`allowedDepositor`) and the parallel with `SwapAllowlistExtension` strongly indicate `sender` is the intended subject.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached.
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)`. Bob is **not** allowlisted.
3. Bob calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
4. The pool calls `extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. The extension checks `allowedDepositor[pool][Alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` mints shares to Alice's position; Bob's tokens are pulled via the callback.
7. Bob has deposited into a pool he is not authorized to access. The allowlist is defeated. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
