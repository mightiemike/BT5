Looking at the extension hook plumbing, I need to trace the exact identity checked by each guard against the actual actor performing the operation.

Let me examine the critical path: `pool.addLiquidity` → `_beforeAddLiquidity` → `DepositAllowlistExtension.beforeAddLiquidity`. [1](#0-0) [2](#0-1) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`. The extension ignores `sender` entirely and only checks `owner`. Since `addLiquidity` imposes no constraint that `msg.sender == owner`, any caller can supply any `owner` address. [3](#0-2) 

---

### Title
`DepositAllowlistExtension` checks `owner` (position recipient) instead of `sender` (actual depositor), allowing any unprivileged caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` gates the `owner` argument (the address that will receive LP shares) rather than the `sender` argument (the address that actually called `pool.addLiquidity` and will pay tokens via the callback). Because `MetricOmmPool.addLiquidity` places no constraint that `msg.sender == owner`, any non-allowlisted caller can name an allowlisted address as `owner`, pass the guard, pay tokens through the callback, and credit LP shares to the allowlisted address — effectively depositing into a permissioned pool without being on the allowlist.

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`:

```solidity
// MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension:

```solidity
// ExtensionCalling.sol:95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (first argument, `address,`) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` here is the pool (correct for the mapping key). `owner` is the position recipient — not the payer. The actual depositor identity (`sender`) is never consulted.

The `MetricOmmPoolLiquidityAdder` makes this trivially reachable: `addLiquidityExactShares(pool, owner, ...)` accepts a caller-supplied `owner` with only a zero-address check, then calls `pool.addLiquidity(positionOwner, ...)` where `positionOwner` is the attacker-chosen address. [4](#0-3) [5](#0-4) 

### Impact Explanation

The deposit allowlist is the primary access-control mechanism for permissioned pools. Its complete bypass means:

- Any non-allowlisted address can add liquidity to a pool that the admin intended to restrict (e.g., KYC-gated, institutional-only, or whitelist-only pools).
- A non-allowlisted attacker who colludes with any single allowlisted address can route unlimited deposits through that address, diluting existing LPs' fee share and altering pool composition.
- Even without collusion, an attacker can force LP positions onto allowlisted addresses (griefing), potentially creating unwanted tax or compliance obligations for those addresses.
- The `addLiquidityWeighted` probe path also runs `beforeAddLiquidity` with the attacker-chosen `owner`, so the bypass applies to both exact-share and weighted deposit flows.

This breaks the admin-boundary invariant: an admin-configured guard is bypassed by an unprivileged path with no special role or token required beyond having tokens to deposit.

### Likelihood Explanation

- The bypass requires no privileged role, no flash loan, and no oracle manipulation.
- The attacker only needs to know one allowlisted address (publicly readable from `allowedDepositor` mapping) and have tokens to deposit.
- Both the direct pool path (`pool.addLiquidity(allowlisted_addr, ...)`) and the periphery path (`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlisted_addr, ...)`) are reachable by any EOA.
- Likelihood is **high** given the trivial preconditions.

### Recommendation

Check `sender` (the actual depositor/payer) instead of `owner` (the position recipient) in `beforeAddLiquidity`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the actual swapper) rather than `recipient`. [6](#0-5) 

### Proof of Concept

```
Setup:
  pool P has DepositAllowlistExtension E
  allowedDepositor[P][alice] = true
  bob is NOT in allowedDepositor[P]

Attack (direct pool path):
  bob calls: P.addLiquidity(
      owner    = alice,   // allowlisted — passes the guard
      salt     = 0,
      deltas   = [...],
      callbackData = ...,
      extensionData = ...
  )

  Pool calls: E.beforeAddLiquidity(bob, alice, ...)
  Extension checks: allowedDepositor[P][alice] == true  → PASSES
  Pool calls: bob.metricOmmModifyLiquidityCallback(amount0, amount1, ...)
  Bob pays tokens from his own balance.
  Alice receives LP shares in bin positions.

Result:
  bob (non-allowlisted) has successfully deposited into the permissioned pool.
  The deposit allowlist did not block him.

Attack (periphery path, no custom callback needed):
  bob calls: MetricOmmPoolLiquidityAdder.addLiquidityExactShares(
      pool  = P,
      owner = alice,   // allowlisted
      ...
  )
  Same outcome — bob pays, alice receives shares, guard never fires on bob.
``` [2](#0-1) [7](#0-6) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
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
