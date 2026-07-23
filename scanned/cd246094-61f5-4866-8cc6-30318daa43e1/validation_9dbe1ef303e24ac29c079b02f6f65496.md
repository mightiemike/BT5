### Title
`DepositAllowlistExtension.beforeAddLiquidity` gates position `owner` instead of the actual caller (`sender`), allowing any unprivileged address to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension` is intended to restrict which addresses can add liquidity to a pool. Its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual caller/payer) and checks only `owner` (the position recipient). Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` parameter validated only for non-zero, any unprivileged address can bypass the allowlist by supplying an allowlisted address as `owner`.

---

### Finding Description

In `DepositAllowlistExtension.beforeAddLiquidity`, the first argument (`sender`) is unnamed and ignored; only `owner` is checked: [1](#0-0) 

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as the position recipient: [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` and validates only that it is non-zero: [3](#0-2) [4](#0-3) 

**Attack path:**

1. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — `alice` is allowlisted.
2. `bob` (not allowlisted) calls `liquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")`.
3. `_validateOwner(alice)` passes (`alice != address(0)`).
4. Adder calls `pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), "")`.
5. Pool calls `_beforeAddLiquidity(adder, alice, salt, deltas, "")`.
6. Extension checks `allowedDepositor[pool][alice]` → `true` → passes.
7. LP shares minted to `alice`; tokens pulled from `bob` via callback.
8. `bob` has added liquidity to a restricted pool without being allowlisted.

The same bypass applies to `addLiquidityWeighted` (both the probe and paying calls use the caller-supplied `owner`): [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is bypassed entirely. Any unprivileged address can add liquidity to a pool the admin intended to restrict, by routing through the liquidity adder with an allowlisted `owner`. The pool receives liquidity from an unprivileged source, breaking the admin-configured access-control invariant. This is an admin-boundary break: the pool admin's allowlist is circumvented by an unprivileged path with no special role required.

---

### Likelihood Explanation

Medium. The attacker only needs to know one allowlisted address (readable on-chain from `allowedDepositor` mapping) and be willing to pay tokens (which are minted as LP shares to the allowlisted address). No special privilege is required; any EOA or contract can trigger this.

---

### Recommendation

Check `sender` (the actual caller/payer) instead of `owner` in `beforeAddLiquidity`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`sender` is `msg.sender` of the pool's `addLiquidity` call — the entity actually providing tokens — which is the correct identity to gate.

---

### Proof of Concept

```solidity
// 1. Pool deployed with DepositAllowlistExtension; alice is allowlisted
depositAllowlist.setAllowedToDeposit(pool, alice, true);

// 2. bob (not allowlisted) bypasses the gate via the liquidity adder
liquidityAdder.addLiquidityExactShares(
    pool,
    alice,   // owner = allowlisted address
    salt,
    deltas,
    max0,
    max1,
    ""
);
// Result: extension checks allowedDepositor[pool][alice] == true → passes
// LP shares minted to alice, tokens pulled from bob
// bob has deposited into a restricted pool without being allowlisted
``` [1](#0-0) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-115)
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
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
