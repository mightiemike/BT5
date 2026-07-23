### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` parameter (the LP position owner) rather than the `sender` parameter (the actual transaction initiator). Because `owner` is a freely caller-controlled argument to `MetricOmmPool.addLiquidity`, any unprivileged address can bypass the deposit allowlist entirely by setting `owner` to any address that is already on the allowlist.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address from the caller and forwards both `msg.sender` (as `sender`) and `owner` to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` encodes both and dispatches them to every registered extension:

```solidity
// ExtensionCalling.sol lines 95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (the first parameter is unnamed) and gates only on `owner`:

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

Because `owner` is a parameter the caller supplies freely, any address can pass the check by nominating an already-allowed address as `owner`. The pool then accepts the deposit, records the LP position under the nominated `owner`, and the actual caller's tokens are irrevocably transferred into the pool.

Contrast with `SwapAllowlistExtension`, which correctly checks `sender` (the actual caller):

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry between the two sibling extensions confirms the deposit extension is checking the wrong address.

---

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to enforce access control on liquidity provision (e.g., KYC-gated pools, institutional-only pools, or pools with regulatory restrictions). The bypass means:

1. **Allowlist is completely ineffective**: Any unprivileged address can deposit into a restricted pool by setting `owner` to any address that appears in `allowedDepositor[pool]`.
2. **Unauthorized funds enter the pool**: The pool's liquidity composition changes without the admin's authorization, violating the core invariant that only permitted depositors can add liquidity.
3. **Forced LP position on the nominated owner**: The allowed address receives LP shares it never requested. While it can withdraw them, this constitutes an unsolicited change to its on-chain state and pool exposure.
4. **Attacker loses deposited tokens**: The attacker's tokens are locked under the nominated owner's position key (since `removeLiquidity` enforces `msg.sender == owner`), making this a griefing vector where the attacker sacrifices funds to violate the pool's access policy.

---

### Likelihood Explanation

**High.** The bypass requires no special permissions, no flash loans, no oracle manipulation, and no multi-step setup. Any EOA or contract can call `addLiquidity` with `owner` set to any address already on the allowlist. The allowlist is trivially enumerable from on-chain events (`AllowedToDepositSet`). The attack is executable in a single transaction.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller) instead of `owner`, mirroring the pattern used by `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
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

Also rename `setAllowedToDeposit` / `allowedDepositor` mappings to reflect that they key on the depositing sender, not the position owner, to avoid future confusion.

---

### Proof of Concept

**Setup:**
- Pool `P` has `DepositAllowlistExtension` configured.
- Pool admin calls `setAllowedToDeposit(P, Alice, true)`.
- Bob is **not** on the allowlist.

**Attack:**
1. Bob calls `P.addLiquidity(owner = Alice, salt = 0, deltas = ..., callbackData = ..., extensionData = "")`.
2. Pool calls `_beforeAddLiquidity(msg.sender=Bob, owner=Alice, ...)`.
3. Extension evaluates `allowedDepositor[P][Alice]` → `true` → **no revert**.
4. `LiquidityLib.addLiquidity` executes: Bob's tokens are pulled via the swap callback, and the LP position is recorded under `(Alice, salt=0)`.
5. Bob cannot withdraw (enforced by `if (msg.sender != owner) revert NotPositionOwner()` in `removeLiquidity`).
6. Alice holds an LP position she never requested; the pool has accepted unauthorized liquidity.

**Result:** The deposit allowlist is fully bypassed. The pool admin's access control is defeated by a single unprivileged call. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
