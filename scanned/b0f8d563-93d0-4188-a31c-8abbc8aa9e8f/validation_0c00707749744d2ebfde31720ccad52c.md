The bypass is real and fully traceable through the code. Here is the complete analysis:

---

### Title
`DepositAllowlistExtension` checks `owner` (position recipient) instead of `sender` (actual caller), allowing any non-allowlisted actor to deposit into a curated pool — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking whether the `owner` parameter is allowlisted, not whether the actual transaction initiator (`sender`) is allowlisted. Because `addLiquidityWeighted` (and `addLiquidityExactShares`) accept a caller-controlled `owner` argument, any non-allowlisted actor can bypass the allowlist by supplying an allowlisted address as `owner`.

---

### Finding Description

**The guard (line 38):**

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`msg.sender` here is the pool (the extension is called by the pool). The first parameter — `sender`, the actual initiator of `addLiquidity` — is **silently discarded** (unnamed `address,`). The check is purely on `owner`. [1](#0-0) 

**The pool passes `msg.sender` as `sender` to the extension:**

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

So the extension receives `sender = MetricOmmPoolLiquidityAdder` (ignored) and `owner = attacker-supplied address` (checked).

**The attacker-controlled `owner` path through `addLiquidityWeighted`:**

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol
function addLiquidityWeighted(address pool, address owner, ...) external payable {
    _validateOwner(owner);   // only rejects address(0)
    ...
    // PROBE: pool.addLiquidity(owner=allowlisted, KIND_PROBE, ...)
    try IMetricOmmPoolActions(pool).addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) ...
    // PAY:  pool.addLiquidity(owner=allowlisted, KIND_PAY, ...)
    return _addLiquidity(pool, owner, salt, scaled, msg.sender, ...);
}
``` [3](#0-2) 

`_validateOwner` only rejects `address(0)`: [4](#0-3) 

**Full bypass path:**

1. Attacker (non-allowlisted) calls `addLiquidityWeighted(pool, allowlisted_address, salt, ...)`
2. Probe phase: `pool.addLiquidity(owner=allowlisted_address, KIND_PROBE, ...)` → extension checks `allowedDepositor[pool][allowlisted_address]` → **passes**
3. Pay phase: `pool.addLiquidity(owner=allowlisted_address, KIND_PAY, ...)` → extension checks same → **passes**
4. Tokens pulled from attacker (`msg.sender` stored as `payer` in transient context)
5. LP shares minted to `allowlisted_address`

The same bypass applies to `addLiquidityExactShares(pool, owner, ...)`. [5](#0-4) 

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism for pool curation — restricting which addresses may add liquidity. The bypass completely nullifies this control: any non-allowlisted actor can deposit into a curated pool by naming an allowlisted address as `owner`. The allowlisted address receives unsolicited LP shares; the attacker pays the tokens. The pool's curation invariant (`allowedDepositor[pool][actual_depositor]`) is broken for every deposit path that accepts a caller-supplied `owner`.

---

### Likelihood Explanation

The attack requires no special privileges, no flash loan, and no oracle manipulation. Any EOA can call `addLiquidityWeighted` or `addLiquidityExactShares` with an arbitrary `owner`. The allowlisted address to use as `owner` is publicly readable from `allowedDepositor` events or the mapping itself. Likelihood is high.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual initiator) rather than `owner` (the position recipient):

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to allowlist by position recipient (`owner`), the design must be documented clearly and the allowlist admin must understand that any actor can pay tokens on behalf of an allowlisted address.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_nonAllowlistedCanBypassDepositAllowlist() public {
    address allowlisted = makeAddr("allowlisted");
    address attacker    = makeAddr("attacker");

    // Pool admin allowlists only `allowlisted`
    depositExtension.setAllowedToDeposit(address(pool), allowlisted, true);

    // Fund attacker and approve adder
    token0.mint(attacker, 1_000_000);
    token1.mint(attacker, 1_000_000);
    vm.startPrank(attacker);
    token0.approve(address(adder), type(uint256).max);
    token1.approve(address(adder), type(uint256).max);

    LiquidityDelta memory w = /* single-bin weight delta */;
    (int8 minB, uint104 minP, int8 maxB, uint104 maxP) = unconstrainedBounds();

    // Attacker sets owner = allowlisted — both probe and pay phases pass
    adder.addLiquidityWeighted(
        address(pool), allowlisted, 0, w,
        500_000, 500_000, minB, minP, maxB, maxP, ""
    );
    vm.stopPrank();

    // LP shares minted to allowlisted, attacker paid — allowlist bypassed
    assertGt(stateView.positionBinShares(address(pool), allowlisted, 0, binIdx), 0);
}
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L100-116)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
