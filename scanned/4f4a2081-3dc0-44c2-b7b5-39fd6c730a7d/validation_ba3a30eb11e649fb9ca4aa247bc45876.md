### Title
DepositAllowlistExtension Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Addresses to Bypass the Deposit Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual depositor who provides tokens) and instead validates the caller-supplied `owner` parameter (the LP position recipient). Because `addLiquidity` imposes no requirement that `msg.sender == owner`, any non-allowlisted address can bypass the deposit guard by naming an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its hook signature receives `sender` as the first argument and `owner` as the second, but the implementation discards `sender` (unnamed `address,`) and checks `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`sender` is `msg.sender` of the pool's `addLiquidity` call — the address that must satisfy the callback and actually transfer tokens into the pool. `owner` is a free caller-supplied parameter that only determines who receives the LP position shares.

`MetricOmmPool.addLiquidity` passes both without enforcing equality:

```solidity
// metric-core/contracts/MetricOmmPool.sol  lines 182-196
function addLiquidity(address owner, ...) external nonReentrant(...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
        _liquidityContext(), owner, salt, ...
    );
    _afterAddLiquidity(msg.sender, owner, ...);
}
```

The LP position key is `keccak256(abi.encode(owner, salt, bin))`, so shares are credited to `owner`, not to `msg.sender`.

The sibling `SwapAllowlistExtension` correctly checks `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  lines 31-40
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry confirms the deposit hook checks the wrong field.

---

### Impact Explanation

Any non-allowlisted address can deposit into a permissioned pool by supplying an allowlisted address as `owner`. The non-allowlisted depositor provides the tokens (via the modify-liquidity callback); the allowlisted owner receives the LP shares and can immediately call `removeLiquidity` to withdraw them. The pool's access-control invariant — that only approved depositors may add liquidity — is fully broken. Pools configured for KYC-gated, institutional, or regulatory-restricted participation are silently open to any depositor.

---

### Likelihood Explanation

Exploitation requires no special privilege: any address can call `addLiquidity` with an arbitrary `owner`. The allowlisted address need not cooperate; the attacker simply names any known allowlisted address. The bypass is deterministic and requires a single transaction.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

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

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured in `beforeAddLiquidity` order.
2. Pool admin calls `setAllowedToDeposit(pool, Alice, true)`. Bob is **not** allowlisted.
3. Bob calls `pool.addLiquidity(owner=Alice, salt=0, deltas=..., callbackData=..., extensionData=...)`.
4. Pool calls `extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` credits shares to `positionBinKey(Alice, 0, bin)`.
7. Bob's `metricOmmModifyLiquidityCallback` is invoked; Bob transfers tokens into the pool.
8. Alice now holds LP shares she did not pay for; she calls `removeLiquidity` and withdraws Bob's tokens.
9. Bob has bypassed the allowlist and lost funds to Alice; the pool's permissioning is broken.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-40)
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
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L70-72)
```text
          // safe because -128 <= LOWEST_BIN <= HIGHEST_BIN <= 127 (enforced by factory)
          // forge-lint: disable-next-line(unsafe-typecast)
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
```
