### Title
`DepositAllowlistExtension` gates `owner` (position recipient) instead of the actual token payer, allowing any un-allowlisted address to bypass the deposit guard via `MetricOmmPoolLiquidityAdder` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` argument and checks only `owner` (the position recipient). When `MetricOmmPoolLiquidityAdder` is the caller, `sender` = the adder contract and `owner` = a caller-supplied address. Any un-allowlisted address can pass an allowlisted address as `owner`, causing the guard to approve a deposit whose actual token provider is never checked.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is declared as:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The first `address` parameter (`sender`) is silently discarded. Only `owner` is checked.

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (with explicit owner) accepts any non-zero `owner` from the caller and forwards it directly to the pool:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,   // caller-supplied; only validated != address(0)
    ...
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);   // only rejects address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

Inside `_addLiquidity`, the adder calls `pool.addLiquidity(positionOwner, ...)`: [4](#0-3) 

At the moment the pool fires `_beforeAddLiquidity`:
- `sender` = `MetricOmmPoolLiquidityAdder` (adder contract address — ignored by the extension)
- `owner` = the caller-supplied address (checked by the extension)
- `payer` = actual `msg.sender` of the adder call (stored only in transient context, **never passed to any extension**)

The actual token provider (`payer`) is structurally invisible to all extensions. The extension can only observe the adder's address (as `sender`) or the caller-supplied `owner`. Neither is the real token provider.

The `addLiquidityWeighted` path has the same structure — the probe call also passes `owner` through `beforeAddLiquidity`: [5](#0-4) 

---

### Impact Explanation

An un-allowlisted address (Bob) can:

1. Call `adder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, data)` where `alice` is allowlisted.
2. The pool fires `beforeAddLiquidity(sender=adder, owner=alice, ...)`.
3. The extension checks `allowedDepositor[pool][alice]` → `true` → passes.
4. Bob's tokens are pulled (via the adder's callback), and LP shares are credited to Alice.

Bob has effectively provided liquidity to a restricted pool despite being un-allowlisted. With a colluding allowlisted address (Alice), Bob can later receive the withdrawn tokens off-chain, completing a full allowlist bypass. The pool receives tokens from an un-allowlisted source, breaking the invariant the pool admin configured.

This is an **admin-boundary break**: the pool admin's deposit allowlist is bypassed by an unprivileged path through the public periphery adder.

---

### Likelihood Explanation

- The `MetricOmmPoolLiquidityAdder` is a public, permissionless periphery contract.
- The only validation on `owner` is `!= address(0)`.
- Any address can call `addLiquidityExactShares` with any allowlisted `owner`.
- No special role or privileged access is required.
- The bypass is reachable on every pool that uses `DepositAllowlistExtension` with a non-empty allowlist.

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` should check `sender` (the actual caller of `pool.addLiquidity`) rather than `owner`. For direct pool calls, `sender` = the depositing EOA. For adder-mediated calls, the adder itself must be allowlisted, and the adder should enforce its own caller check before forwarding.

Alternatively, the adder should pass the actual payer as `extensionData` so extensions can decode and verify it, or the pool interface should expose a dedicated `payer` field to extensions.

The corrected extension check:

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

```
Setup:
  - Pool deployed with DepositAllowlistExtension
  - alice is allowlisted: allowedDepositor[pool][alice] = true
  - bob is NOT allowlisted

Attack:
  1. bob calls:
       adder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")
  2. adder calls:
       pool.addLiquidity(owner=alice, ..., callbackData=KIND_PAY)
  3. pool calls:
       extension.beforeAddLiquidity(sender=adder, owner=alice, ...)
  4. Extension checks allowedDepositor[pool][alice] → true → no revert
  5. Pool mints LP shares to alice
  6. Pool calls adder callback; adder pulls tokens from bob (payer)

Result:
  - bob's tokens enter the pool (un-allowlisted source)
  - alice holds LP shares
  - alice + bob split proceeds off-chain
  - DepositAllowlistExtension never checked bob's address
``` [1](#0-0) [3](#0-2) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-114)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
