### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unauthorized depositor to bypass the pool's deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller of `addLiquidity`) and instead validates the caller-controlled `owner` parameter. Because `owner` is freely chosen by the depositor, any address not on the allowlist can deposit into a restricted pool by nominating an allowlisted address as the position owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` forwards two distinct addresses to the extension hook:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the actual depositor; `owner` is a parameter supplied by that depositor and recorded as the position holder. The extension receives them as the first and second arguments respectively:

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
```

The first parameter (`sender`) is **unnamed and discarded**. The guard checks `allowedDepositor[pool][owner]`, where `owner` is a value the depositor chose. An attacker who knows any allowlisted address `bob` can call:

```
pool.addLiquidity(bob, salt, deltas, callbackData, extensionData)
```

The pool invokes `beforeAddLiquidity(attacker, bob, ...)`. The extension evaluates `allowedDepositor[pool][bob]` → `true` → passes. The attacker pays the tokens in the callback; the position is recorded under `bob`. Bob can subsequently remove the liquidity and return the tokens, completing a full round-trip that bypasses the allowlist entirely.

The same path is available through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, bob, ...)` and `addLiquidityWeighted(pool, bob, ...)`, where `owner` is an explicit caller-supplied argument.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., KYC gating, whitelist-only pools). The bypass allows:

1. **Unauthorized liquidity injection** — any actor can deposit into a pool they are explicitly excluded from.
2. **Pool state manipulation** — an attacker can place liquidity in specific bins to skew the marginal price seen by swappers, affecting swap execution quality and LP returns.
3. **Admin-boundary break** — the pool admin's access-control intent is completely nullified without any privileged action.

---

### Likelihood Explanation

- Any pool that deploys `DepositAllowlistExtension` with a non-trivial allowlist is affected.
- The attacker only needs to know one allowlisted address (publicly readable from `allowedDepositor` mapping or on-chain events).
- No special permissions, flash loans, or oracle manipulation are required.
- The `MetricOmmPoolLiquidityAdder` provides a convenient, gas-efficient entry point.

---

### Recommendation

Replace the unnamed first parameter with `sender` and check it instead of (or in addition to) `owner`:

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

`sender` is `msg.sender` of the pool's `addLiquidity` call — the actual depositor — and is the correct actor to gate.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = false   // Alice is NOT allowed
  allowedDepositor[pool][bob]   = true    // Bob IS allowed

Attack:
  1. Alice (EOA or contract) calls:
       pool.addLiquidity(bob, salt, deltas, callbackData, extensionData)
     msg.sender = Alice, owner = bob

  2. Pool calls:
       _beforeAddLiquidity(Alice, bob, ...)
     → extension checks allowedDepositor[pool][bob] → true → no revert

  3. Alice pays tokens in metricOmmModifyLiquidityCallback.
     Position is recorded under bob.

  4. Bob calls pool.removeLiquidity(bob, salt, deltas, "")
     Tokens returned to bob.

  5. Bob transfers tokens back to Alice out-of-band.

Result: Alice deposited into a pool she is explicitly excluded from,
        with zero on-chain evidence linking her to the deposit.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
