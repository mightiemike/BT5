### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, enabling allowlist bypass via owner/salt separation — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**, but its `beforeAddLiquidity` hook silently discards the `sender` argument and enforces the allowlist against `owner` instead. Because `owner` is a free caller-supplied parameter and `removeLiquidity` enforces `msg.sender == owner`, the two roles are fully separable. An unprivileged depositor can bypass the allowlist by naming any allowlisted address as `owner`; conversely, an allowlisted router cannot deposit on behalf of non-allowlisted users, breaking the intended delegation pattern.

---

### Finding Description

In `MetricOmmPool.addLiquidity`, the pool calls the extension hook with `sender = msg.sender` (the actual token-providing caller) and `owner` = the arbitrary address supplied by the caller:

```solidity
// MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`DepositAllowlistExtension.beforeAddLiquidity` receives both but discards `sender` (unnamed first parameter) and enforces the allowlist only on `owner`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` and discards `recipient`, establishing the intended pattern:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The asymmetry is the root cause. `owner` is a free parameter; `sender` is the address that actually provides tokens via the swap callback and is the only address the pool can hold accountable for the deposit.

**Bypass path:**
1. Pool is configured with `DepositAllowlistExtension`; only address `A` is allowlisted.
2. Unauthorized address `B` calls `pool.addLiquidity(owner=A, salt=X, ...)`.
3. Extension checks `allowedDepositor[pool][A]` → `true` → passes.
4. `LiquidityLib.addLiquidity` executes; the callback fires on `B` (msg.sender), pulling `B`'s tokens.
5. Position `(A, X)` is minted. Only `A` can call `removeLiquidity` (enforced by `msg.sender == owner`).
6. `B` has deposited into a restricted pool in violation of the allowlist; `A` receives a position it did not request.

**False-restriction path:**
1. Pool admin allowlists a router contract `R` as the depositor.
2. `R` calls `pool.addLiquidity(owner=user, ...)` on behalf of `user`.
3. Extension checks `allowedDepositor[pool][user]` → `false` → reverts.
4. The allowlisted router cannot deposit on behalf of any non-allowlisted owner, making the router-based deposit flow entirely unusable.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., KYC/compliance gating, private institutional pools). Checking `owner` instead of `sender` breaks this invariant in both directions: unauthorized depositors can bypass the gate, and allowlisted routers are incorrectly blocked. This constitutes an admin-boundary break (an unprivileged path bypasses a pool-admin-configured access control) and broken core liquidity functionality (allowlisted router-based deposits are rendered unusable).

---

### Likelihood Explanation

The bypass is reachable by any address without special privileges: calling `addLiquidity` with `owner` set to any allowlisted address is sufficient. The false-restriction path is triggered in the normal router-delegation pattern whenever the pool admin allowlists a router rather than individual users. Both paths require no privileged setup beyond the pool existing with the extension configured.

---

### Recommendation

Mirror the pattern used by `SwapAllowlistExtension`: check `sender`, not `owner`.

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

```solidity
// Assume pool has DepositAllowlistExtension; only `allowedUser` is allowlisted.
address allowedUser  = address(0xA);
address attacker     = address(0xB); // NOT allowlisted

// Attacker calls addLiquidity naming the allowlisted address as owner.
// Extension checks allowedDepositor[pool][allowedUser] == true → passes.
// Callback fires on attacker; attacker's tokens are pulled.
// Position (allowedUser, salt) is minted — allowlist fully bypassed.
vm.prank(attacker);
pool.addLiquidity(
    allowedUser,   // owner — passes the (wrong) allowlist check
    uint80(1),     // salt
    deltas,
    callbackData,
    extensionData
);

// allowedUser now holds a position funded by attacker's tokens.
// Attacker deposited into a restricted pool without being allowlisted.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
