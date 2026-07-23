### Title
`DepositAllowlistExtension` checks position `owner` instead of actual token payer, allowing non-allowlisted actors to deposit tokens into curated pools — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking whether the position `owner` argument is allowlisted, but ignores the `sender` parameter entirely. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any caller supply an arbitrary `owner` while the actual token payer is always `msg.sender` (stored separately in transient context), a non-allowlisted actor can deposit tokens into a curated pool by nominating an allowlisted address as the position owner.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is defined as:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first parameter (`sender`) is explicitly unnamed and discarded. The guard only checks `allowedDepositor[pool][owner]`. [1](#0-0) 

The pool passes both `sender` (the direct caller of `pool.addLiquidity`) and `owner` (the position owner argument) to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
_callExtensionsInOrder(
  BEFORE_ADD_LIQUIDITY_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-controlled `owner` and stores `msg.sender` as the payer in transient storage — these two addresses are fully independent:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(
    address pool, address owner, uint80 salt, LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1, bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
}
``` [3](#0-2) 

The callback then pulls tokens from the stored payer (B), not from `owner` (A):

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L172-176
if (amount0Delta > 0) {
    pay(token0, payer, msg.sender, amount0Delta);
}
if (amount1Delta > 0) {
    pay(token1, payer, msg.sender, amount1Delta);
}
``` [4](#0-3) 

**Attack path:**
1. Pool admin allowlists only address A via `setAllowedToDeposit(pool, A, true)`.
2. Attacker B (not allowlisted) calls `adder.addLiquidityExactShares(pool, owner=A, salt, deltas, max0, max1, "")`.
3. LiquidityAdder calls `pool.addLiquidity(owner=A, ...)` with `msg.sender = LiquidityAdder`.
4. Pool calls extension: `beforeAddLiquidity(sender=LiquidityAdder, owner=A, ...)`.
5. Extension checks `allowedDepositor[pool][A]` → true → **passes**.
6. Callback pulls tokens from B (the transient payer), deposits them into the pool.
7. Position is recorded under A; B's tokens are now in the curated pool.

Note: even if the extension checked `sender` instead of `owner`, it would see the LiquidityAdder contract address — not B — so the router indirection makes the actual payer permanently invisible to the extension.

---

### Impact Explanation

The `DepositAllowlistExtension` is designed to enforce that only approved actors supply tokens to a curated pool (e.g., for KYC/regulatory compliance or controlled liquidity programs). The bypass allows any non-allowlisted address to inject tokens into the pool under an allowlisted owner's position. The pool's curation invariant is broken: token inflow is no longer restricted to approved depositors. The attacker forfeits their tokens (no financial gain), but the pool receives unauthorized token inflow and A receives an unsolicited LP position.

---

### Likelihood Explanation

The `addLiquidityExactShares(pool, owner, ...)` overload is a public, documented entry point explicitly designed to allow `owner ≠ msg.sender`. Any actor who knows an allowlisted address can execute this with zero preconditions beyond having the tokens and approval set on the LiquidityAdder. [5](#0-4) 

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` should check the `sender` parameter (the direct caller of `pool.addLiquidity`) rather than `owner`. For the router path, the LiquidityAdder should forward the actual user address as `sender` to the pool, or the extension should check both `sender` and `owner`. Alternatively, the pool admin documentation must explicitly clarify that the allowlist gates position ownership, not token sourcing, and pool admins relying on token-source gating must use a different mechanism.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_nonAllowlistedPayerBypassesDepositAllowlist() public {
    address A = makeAddr("allowlisted");
    address B = makeAddr("attacker");

    // Pool admin allowlists only A
    vm.prank(poolAdmin);
    depositExtension.setAllowedToDeposit(address(pool), A, true);

    // Fund and approve B on the LiquidityAdder
    deal(address(token0), B, 1_000 ether);
    deal(address(token1), B, 1_000 ether);
    vm.startPrank(B);
    token0.approve(address(adder), type(uint256).max);
    token1.approve(address(adder), type(uint256).max);

    // B deposits with owner=A — extension checks A (allowlisted), passes
    LiquidityDelta memory d = /* some valid delta */;
    adder.addLiquidityExactShares(address(pool), A, 1, d, type(uint256).max, type(uint256).max, "");
    vm.stopPrank();

    // B's tokens were pulled, A has a position
    assertGt(stateView.positionBinShares(address(pool), A, 1, binIdx), 0);
    assertLt(token0.balanceOf(B), 1_000 ether); // B paid

    // Direct attempt by B as owner reverts
    vm.prank(B);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    pool.addLiquidity(B, 2, d, "", "");
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-176)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L13-14)
```text
/// @dev The position `owner` may differ from `msg.sender`, but token pulls in callback are always sourced from
///      `msg.sender` that initiated the add call.
```
