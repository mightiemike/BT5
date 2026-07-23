### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and gates on `owner` instead. Because `addLiquidity` accepts an arbitrary `owner` address with no requirement that `msg.sender == owner`, any unprivileged caller can bypass the allowlist by naming an already-allowlisted address as `owner`.

---

### Finding Description

The pool calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both the actual caller (`sender`) and the position-owner (`owner`) to every registered extension. [1](#0-0) 

`ExtensionCalling` faithfully encodes both arguments: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then silently discards `sender` (first positional argument, left unnamed) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

The admin-facing setter names its argument `depositor`, confirming the intent was to gate the actual depositing address: [4](#0-3) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly reads and checks `sender` (first positional argument):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [5](#0-4) 

The structural mismatch — `sender` checked in the swap guard, `owner` checked in the deposit guard — is the direct analog of the external report's wrong-variable bug.

---

### Impact Explanation

**Impact: Medium**

The pool admin deploys `DepositAllowlistExtension` specifically to restrict which addresses may add liquidity (e.g., KYC-gated LPs, regulatory compliance, or manipulation prevention). Because the check is on `owner` rather than `sender`:

1. **Allowlist bypass** — Any address not on the allowlist calls `pool.addLiquidity(allowlistedOwner, salt, deltas, ...)`. The guard reads `allowedDepositor[pool][allowlistedOwner]` → `true` and passes. The caller provides tokens via the swap callback; the position is minted to `allowlistedOwner`. The admin-configured guard is fully bypassed by an unprivileged path.

2. **False block** — A legitimately allowlisted router or aggregator that calls `addLiquidity` on behalf of a user (where `owner != sender`) is blocked because `owner` is not on the list, breaking the intended deposit flow for that actor.

This is an admin-boundary break: the pool admin's configured access-control guard is circumvented by any unprivileged caller, matching the contest's "factory/oracle role checks are bypassed by an unprivileged path" criterion.

---

### Likelihood Explanation

**Likelihood: Low-Medium**

The bypass requires only that the attacker know one allowlisted address (trivially discoverable from on-chain `AllowedToDepositSet` events) and call `addLiquidity` with that address as `owner`. No special privilege, flash loan, or oracle manipulation is needed. The attacker does lose the deposited tokens (position accrues to `allowlistedOwner`), which limits economic incentive for direct exploitation, but the guard is structurally broken for any pool relying on it.

---

### Recommendation

Mirror the pattern used in `SwapAllowlistExtension`: name and check `sender` (the actual caller), not `owner`.

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** allowlisted.
2. Bob calls `pool.addLiquidity(alice /*owner*/, salt, deltas, callbackData, "")`.
3. Pool calls `_beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
4. `DepositAllowlistExtension.beforeAddLiquidity` receives `(bob, alice, ...)`, ignores `bob`, checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. Bob's callback is invoked; Bob transfers tokens into the pool.
6. The position is minted to `alice`. Bob has deposited into a pool he was explicitly barred from, bypassing the admin-configured allowlist entirely. [3](#0-2) [6](#0-5)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

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
