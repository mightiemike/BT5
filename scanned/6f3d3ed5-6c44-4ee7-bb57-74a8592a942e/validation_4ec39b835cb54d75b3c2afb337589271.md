### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist via `MetricOmmPoolLiquidityAdder` — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is intended to gate `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently ignores the `sender` parameter (the actual caller of `pool.addLiquidity`) and instead checks the `owner` parameter (the position owner). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a fully caller-controlled `owner` argument with no restriction beyond `!= address(0)`, any unprivileged address can bypass the deposit allowlist by supplying an allowlisted address as `owner`, depositing tokens into a restricted pool without authorization.

---

### Finding Description

**Root cause — wrong identity checked in the hook:**

In `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and discarded; the allowlist lookup is keyed on `owner` (second parameter):

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

**How `sender` and `owner` are bound in the pool:**

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner`:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

**How `owner` is caller-controlled through the LiquidityAdder:**

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the two-argument overload that accepts an explicit `owner`) passes the caller-supplied value directly to the pool. The only validation is `_validateOwner`, which only checks `owner != address(0)`:

```solidity
// MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(
    address pool,
    address owner,          // ← fully caller-controlled
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);  // ← only checks != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
}
``` [3](#0-2) 

The same pattern applies to `addLiquidityWeighted(address pool, address owner, ...)`: [4](#0-3) 

**The bypass path:**

When Bob (not allowlisted) calls `addLiquidityExactShares(pool, alice, ...)` with `owner = alice` (allowlisted):

1. LiquidityAdder calls `pool.addLiquidity(owner=alice, callbackData=KIND_PAY, ...)`
2. Pool calls `_beforeAddLiquidity(sender=LiquidityAdder, owner=alice, ...)`
3. Extension evaluates `allowedDepositor[pool][alice]` → `true` → hook passes
4. Pool mints LP shares to Alice's position key
5. Pool calls `LiquidityAdder.metricOmmModifyLiquidityCallback(...)`, which pulls tokens from `payer = msg.sender = Bob` [5](#0-4) 

Bob pays the tokens; Alice receives the LP position. The deposit allowlist — the only access-control guard on this pool — is fully bypassed.

---

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to create restricted-access pools (e.g., private LP vaults, KYC-gated pools). With this bypass:

- Any unprivileged address can deposit into a restricted pool by naming any allowlisted address as `owner`.
- The pool admin's access-control invariant is broken: the set of addresses that can add liquidity is no longer bounded by the allowlist.
- Unauthorized depositors can manipulate the pool's bin liquidity distribution, affecting oracle-anchored swap prices and LP share dilution for legitimate LPs.
- Alice (the named owner) receives LP shares she did not authorize; she can withdraw them, but the pool's composition has been altered without the admin's consent.

This is a direct admin-boundary break: a factory-configured guard is bypassed by an unprivileged public path through the periphery.

---

### Likelihood Explanation

**High.** The `MetricOmmPoolLiquidityAdder` is a public, permissionless periphery contract. Any address can call `addLiquidityExactShares` with an arbitrary `owner`. The only prerequisite is knowing one allowlisted address on the target pool, which is observable on-chain from `AllowedToDepositSet` events or by reading `allowedDepositor` storage. No special role, token balance beyond the deposit amount, or privileged access is required.

---

### Recommendation

The `beforeAddLiquidity` hook must gate on the identity that is economically and operationally relevant — the actual depositor — not the position owner. Two options:

1. **Check `sender` instead of `owner`** (gates the direct caller of `pool.addLiquidity`). When users go through the LiquidityAdder, `sender` is the LiquidityAdder address; the pool admin would then allowlist the LiquidityAdder and rely on it to enforce its own caller checks. This is the minimal code change:

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

2. **Require `sender == owner`** in the extension, so that only direct (non-delegated) deposits are permitted on allowlisted pools. This prevents the LiquidityAdder path entirely for restricted pools.

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension
  - Pool admin calls setAllowedToDeposit(pool, alice, true)
  - Bob is NOT allowlisted

Attack:
  1. Bob calls:
       LiquidityAdder.addLiquidityExactShares(
           pool,
           alice,   // ← allowlisted address, not Bob
           salt,
           deltas,
           max0, max1,
           extensionData
       )

  2. LiquidityAdder → pool.addLiquidity(owner=alice, callbackData=KIND_PAY, ...)

  3. Pool → _beforeAddLiquidity(sender=LiquidityAdder, owner=alice, ...)
       Extension: allowedDepositor[pool][alice] == true → PASSES

  4. Pool mints LP shares to positionKey(alice, salt, binIdx)

  5. Pool → LiquidityAdder.metricOmmModifyLiquidityCallback(amount0, amount1, KIND_PAY)
       LiquidityAdder pulls tokens from payer=Bob

Result:
  - Bob's tokens are transferred to the pool ✓
  - Alice holds LP shares she did not authorize ✓
  - Deposit allowlist bypassed: Bob deposited into a pool he is not allowlisted for ✓
``` [1](#0-0) [6](#0-5) [3](#0-2) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-116)
```text
  function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-178)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
