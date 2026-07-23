### Title
`DepositAllowlistExtension` Checks Position `owner` Instead of Actual Depositor `sender`, Allowing Full Allowlist Bypass — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` parameter (the position recipient) against the allowlist, not the `sender` parameter (the address that actually calls `addLiquidity` and pays the tokens via callback). Because `MetricOmmPool.addLiquidity` places no constraint that `msg.sender == owner`, any non-allowlisted address can pass the guard by nominating an allowlisted address as `owner`. With that allowlisted address's cooperation the non-allowlisted depositor can then recover the funds through `removeLiquidity`, achieving a complete bypass of the pool admin's access control.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from the caller and passes both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` encodes them in order:

```solidity
// ExtensionCalling.sol line 97
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as the first (unnamed) parameter and `owner` as the second, but silently discards `sender` and checks only `owner`:

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

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller):

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The inconsistency confirms the check in `DepositAllowlistExtension` is a bug: the wrong parameter is used.

`removeLiquidity` enforces `msg.sender == owner` but has no allowlist hook, so an allowlisted address that received an unwanted position can freely withdraw and return the proceeds to the original non-allowlisted depositor.

---

### Impact Explanation

The deposit allowlist is completely ineffective. Any non-allowlisted address can deposit tokens into the pool by setting `owner` to any allowlisted address. With that address's cooperation the non-allowlisted party recovers the funds through `removeLiquidity`. The pool admin's configured access control — the sole mechanism preventing restricted parties from entering the pool — is bypassed without any privileged action. This breaks the core invariant that only allowlisted depositors can add liquidity, and can violate compliance requirements or pool-security assumptions that motivated the allowlist.

---

### Likelihood Explanation

Exploitation requires only knowledge of one allowlisted address (publicly readable from `allowedDepositor`) and cooperation from that address. No special role, oracle manipulation, or flash loan is needed. The call path is a single direct `addLiquidity` invocation with a crafted `owner` argument.

---

### Recommendation

Rename the first parameter and check `sender` (the actual depositor/caller) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the pattern already used correctly in `SwapAllowlistExtension`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowlisted.
3. Bob (not allowlisted) and Alice agree to collude.
4. Bob calls `pool.addLiquidity(owner = alice, salt = X, deltas, ...)`.
5. The pool calls `extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
6. The guard evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
7. Bob's callback pays the tokens; the position `(alice, X)` is credited to Alice.
8. Alice calls `pool.removeLiquidity(owner = alice, salt = X, ...)` — passes `msg.sender == owner` check — and receives the tokens, which she returns to Bob.
9. Bob has effectively deposited into and withdrawn from the allowlisted pool without ever being on the allowlist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
