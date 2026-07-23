### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. However, its `beforeAddLiquidity` hook validates the `owner` argument (who will own the resulting position) rather than the `sender` argument (who is actually calling `addLiquidity` and paying the tokens). Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address as a caller-supplied parameter, any unprivileged address can bypass the allowlist by setting `owner` to any already-allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

- `sender` = `msg.sender` — the address that is actually calling the pool and will pay tokens via the swap callback.
- `owner` = a caller-supplied parameter — the address that will own the resulting liquidity position.

`DepositAllowlistExtension.beforeAddLiquidity` receives both but validates only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first parameter (`sender`) is silently discarded (unnamed `address`). The guard therefore passes whenever `owner` is allowlisted, regardless of who `sender` is.

Because `addLiquidity` imposes no restriction on who may supply an arbitrary `owner`:

```solidity
function addLiquidity(
    address owner,   // ← caller-supplied, no restriction
    uint80 salt,
    ...
) external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
```

any address can call `pool.addLiquidity(allowlisted_address, ...)` and the extension guard will pass.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting which parties may provide liquidity (e.g., for regulatory compliance, private pools, or KYC-gated venues). The bypass allows any unprivileged address to add liquidity to a pool that is supposed to be restricted, directly violating the admin-boundary invariant. The unauthorized depositor pays the tokens; the allowlisted address receives the position. The pool receives liquidity from a source the admin explicitly excluded, breaking the intended access-control guarantee and potentially violating compliance requirements that the allowlist was deployed to enforce.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any externally-owned account or contract can call `pool.addLiquidity(allowlisted_address, salt, deltas, callbackData, extensionData)` directly. The only prerequisite is knowing one allowlisted address (which is readable from `allowedDepositor` public mapping) and being willing to supply the tokens. Likelihood is high.

---

### Recommendation

Replace the `owner` check with a `sender` check in `DepositAllowlistExtension.beforeAddLiquidity`:

```solidity
// Before (wrong actor):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// After (correct actor):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The `sender` parameter is `msg.sender` of the `addLiquidity` call — the entity actually paying tokens — which is the correct actor to gate.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
2. Bob calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → guard passes.
5. `LiquidityLib.addLiquidity` mints shares keyed to `alice`. Bob's tokens are pulled via the callback on `msg.sender` (Bob).
6. Alice now holds a liquidity position funded by Bob. Bob has successfully deposited into a pool that was supposed to exclude him.

The allowlist invariant — "only allowlisted addresses may deposit" — is broken. The pool admin's access control is bypassed by any unprivileged caller who knows one allowlisted address. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
