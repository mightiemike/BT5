The call chain is fully traceable. Here is the definitive assessment:

---

### Title
`DepositAllowlistExtension` gates by LP position `owner` instead of the actual token payer, allowing any non-allowlisted caller to bypass the deposit restriction — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and checks only `owner`. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` that is independent of `msg.sender` (the actual token payer), any non-allowlisted address can deposit into a restricted pool by naming an allowlisted address as the position owner.

---

### Finding Description

**Call chain:**

```
Bob (non-allowlisted)
  → MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner=alice, ...)
      payer stored as msg.sender = bob
  → pool.addLiquidity(owner=alice, ...)          // MetricOmmPool.sol:191
      _beforeAddLiquidity(sender=LiquidityAdder, owner=alice, ...)
  → ExtensionCalling._beforeAddLiquidity(...)    // ExtensionCalling.sol:97
      encodes (sender=LiquidityAdder, owner=alice, ...)
  → DepositAllowlistExtension.beforeAddLiquidity(_, alice, ...)
      checks allowedDepositor[pool][alice] → true → PASSES
  → callback: bob pays tokens, alice receives LP shares
```

The extension signature is:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
```

The first argument (`sender`) is unnamed and never read. The guard is:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
```

`msg.sender` here is the pool (correct for pool identity). `owner` is the LP position owner supplied by the caller — not the token payer. The actual payer (`bob`) is never checked. [1](#0-0) 

The pool passes `msg.sender` of `addLiquidity` (the LiquidityAdder contract) as `sender`, and the caller-supplied `owner` as `owner`: [2](#0-1) [3](#0-2) 

`addLiquidityExactShares` with an explicit `owner` stores `msg.sender` as the payer but passes the caller-supplied `owner` to the pool: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys `DepositAllowlistExtension` to restrict deposits to a known set of addresses (e.g., KYC'd LPs) achieves no restriction on the actual token payer. Any address can deposit tokens into the pool by specifying any allowlisted address as `owner`. The allowlisted owner receives LP shares they did not request; the non-allowlisted payer's tokens enter the pool. The core access-control invariant of the extension — "only allowlisted depositors may add liquidity" — is broken for every pool using it.

---

### Likelihood Explanation

The exploit requires only a public call to `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with a chosen `owner`. No privileged role, special token, or off-chain data is needed. Any address with token approval can execute it in a single transaction.

---

### Recommendation

Check `sender` (the direct caller of `addLiquidity`) rather than `owner`. Since `sender` as passed by the pool is the LiquidityAdder contract (not the EOA), the extension should either:

1. Check `allowedDepositor[msg.sender][sender]` where `sender` is the direct `addLiquidity` caller, and require the LiquidityAdder to forward the real EOA via `extensionData`; or
2. Require `owner == sender` so the position owner must be the same address that called `addLiquidity`, eliminating the payer/owner split for allowlisted pools.

---

### Proof of Concept

```solidity
// Setup: pool admin allowlists alice, not bob
extension.setAllowedToDeposit(pool, alice, true);
// allowedDepositor[pool][bob] == false

// Bob calls with owner = alice
vm.prank(bob);
adder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "");
// → beforeAddLiquidity checks allowedDepositor[pool][alice] == true → passes
// → bob pays tokens, alice receives LP shares
// → bob (non-allowlisted) successfully deposited into a restricted pool
```

This matches the existing test pattern in the codebase where alice pays and bob receives shares: [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L247-253)
```text
    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 12, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 12, int8(4));
    assertGt(bobShares, 0);
    assertLt(weth.balanceOf(alice), aliceWethBefore);
    assertEq(weth.balanceOf(bob), bobWethBefore);
```
