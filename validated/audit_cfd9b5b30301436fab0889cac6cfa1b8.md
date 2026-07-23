All four factual claims check out against the production code:

1. `beforeAddLiquidity` at L32 has an unnamed first parameter and gates only on `owner` — confirmed. [1](#0-0) 

2. `addLiquidity` at L191 passes `msg.sender` as sender and caller-supplied `owner` as owner — confirmed. [2](#0-1) 

3. `addLiquidity` has no `msg.sender == owner` guard (contrast with `removeLiquidity` at L206 which does) — confirmed. [3](#0-2) 

4. `addLiquidityExactShares` explicitly supports a caller-supplied `owner` distinct from `msg.sender` with only a non-zero check — confirmed. [4](#0-3) 

---

Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of transaction `sender`, allowing any non-allowlisted operator to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (unnamed first parameter) and gates only on `owner`. Because `MetricOmmPool.addLiquidity` accepts any caller-supplied `owner` with no `msg.sender == owner` requirement, any address can bypass the allowlist by naming an allowlisted address as `owner`. The pool admin's deposit restriction is completely ineffective.

## Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` drops the `sender` parameter and checks `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool and `owner` is the position recipient supplied by the caller:

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

The pool calls the hook with `(msg.sender, owner, ...)` at L191, where `msg.sender` is the actual transaction initiator and `owner` is the caller-supplied position recipient. `addLiquidity` imposes no `msg.sender == owner` constraint (unlike `removeLiquidity` at L206 which does). The periphery router `addLiquidityExactShares(pool, owner, ...)` explicitly supports a caller-supplied `owner` distinct from `msg.sender` with only a non-zero check.

Exploit path:
1. Bob (not allowlisted) calls `pool.addLiquidity(owner=alice, ...)` where alice is allowlisted.
2. Pool calls `_beforeAddLiquidity(bob, alice, ...)`.
3. Extension receives `(bob, alice, ...)`, discards `bob`, checks `allowedDepositor[pool][alice]` → `true` → passes.
4. Bob's tokens are pulled via callback; a position is minted under alice's key.

The existing guard (`allowedDepositor[pool][owner]`) is insufficient because it checks the position recipient, not the token payer/transaction initiator.

## Impact Explanation
The deposit allowlist is the pool admin's mechanism to restrict which addresses may add liquidity (e.g., for KYC/compliance or controlled launch). Because the gate checks `owner` rather than `sender`, any non-allowlisted address can inject liquidity into the pool by naming any allowlisted address as `owner`. The pool admin's deposit restriction is completely ineffective — this breaks the core functionality of `DepositAllowlistExtension` and constitutes an admin-boundary break by an unprivileged path.

## Likelihood Explanation
Exploitation requires only knowledge of one allowlisted address (observable on-chain via `AllowedToDepositSet` events) and the ability to call `addLiquidity`. No privileged access, no malicious setup, and no non-standard token behavior is needed. The periphery router makes the `owner != sender` path a first-class supported flow.

## Recommendation
Change `beforeAddLiquidity` to check `sender` (the actual transaction initiator) instead of `owner`:

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

If the intent is to gate on the position owner (not the payer), the contract's NatSpec, event names (`AllowedToDepositSet`), and function names (`setAllowedToDeposit`) should be updated to reflect that, and the security model re-evaluated.

## Proof of Concept
```solidity
function test_operatorBypassesAllowlist() public {
    address alice = makeAddr("alice");   // allowlisted owner
    address bob   = makeAddr("bob");    // NOT allowlisted

    vm.prank(admin);
    depositExtension.setAllowedToDeposit(address(pool), alice, true);

    deal(address(token0), bob, 1e18);
    deal(address(token1), bob, 1e18);
    vm.startPrank(bob);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // Bob calls addLiquidity naming alice as owner — check passes because alice is allowlisted
    LiquidityDelta memory d = /* bins above price */;
    pool.addLiquidity(alice, 0, d, "", "");  // succeeds despite bob not being allowlisted

    // Alice now has a position she didn't initiate; bob bypassed the gate
    assertGt(positionBinShares[alice][...], 0);
    vm.stopPrank();
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
