Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` instead of `sender`, allowing allowlist bypass via `MetricOmmPoolLiquidityAdder` - (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the pool's `msg.sender`, i.e., the actual economic actor calling the pool) and checks only `owner` (the position holder) against the allowlist. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` permits any `msg.sender` to specify an arbitrary `owner` with only a non-zero check, a non-allowlisted payer can route through an allowlisted `owner` address to pass the guard and inject tokens into the pool, fully bypassing the admin-configured deposit restriction.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is defined with the first parameter (`sender`) unnamed and discarded:

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

Here `msg.sender` is the pool (enforced by `onlyPool` in `BaseMetricExtension`), so the check resolves to `allowedDepositor[pool][owner]`. The `sender` argument — which is the pool's `msg.sender`, i.e., the actual caller of `pool.addLiquidity` — is never inspected. [2](#0-1) 

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [3](#0-2) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [4](#0-3) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the owner-specifying overload) allows any `msg.sender` to name an arbitrary `owner`, validated only for non-zero:

```solidity
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [5](#0-4) 

The complete exploit path:
1. Bob (non-allowlisted) calls `liquidityAdder.addLiquidityExactShares(pool, alice, ...)` naming allowlisted `alice` as `owner`.
2. The adder calls `pool.addLiquidity(alice, ...)` with `msg.sender = liquidityAdder`.
3. The pool calls `_beforeAddLiquidity(liquidityAdder, alice, ...)`.
4. The extension receives `sender=liquidityAdder, owner=alice` but checks only `allowedDepositor[pool][alice]` → `true` → guard passes.
5. Bob pays tokens (pulled from Bob in the callback via `pay(token, payer=bob, pool, amount)`); alice receives the LP position. [6](#0-5) 

The existing test `test_exactShares_usesMsgSenderAsPayerNotOwner` confirms the payer/owner separation is an intentional design feature of the adder, making the allowlist check on `owner` alone structurally insufficient. [7](#0-6) 

## Impact Explanation
This is a direct admin-boundary break. The `DepositAllowlistExtension` is the pool admin's primary mechanism to restrict economic participation (token injection, LP position growth). By routing through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with an allowlisted `owner`, any non-allowlisted actor can: (1) inject arbitrary token amounts into the pool bypassing the deposit gate entirely; (2) force LP positions onto allowlisted addresses without their initiation, enabling griefing with downstream accounting or compliance implications; (3) undermine any KYC, compliance, or curated-LP invariant the pool admin intended to enforce. This constitutes a broken core pool access-control mechanism causing potential loss of admin-boundary integrity and fund-impacting bypass of configured pool guards.

## Likelihood Explanation
The `MetricOmmPoolLiquidityAdder` is a public, permissionless periphery contract. The attacker needs only one allowlisted address, trivially discoverable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` storage reads. No special privileges, flash loans, or oracle manipulation are required. The attack is repeatable for any pool using `DepositAllowlistExtension` and requires only token approvals to the adder contract.

## Recommendation
Change `DepositAllowlistExtension.beforeAddLiquidity` to gate on `sender` (the pool's `msg.sender`, i.e., the actual caller of `addLiquidity`) rather than `owner`:

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

If the intent is to gate both the caller and the position holder, check both. Note that when `MetricOmmPoolLiquidityAdder` is used, `sender` will be the adder contract address; pool admins must allowlist the adder or individual EOAs calling the pool directly. Document clearly which identity the allowlist is intended to restrict.

## Proof of Concept

```solidity
// Pool deployed with DepositAllowlistExtension.
// Admin allowlists only alice.
extension.setAllowedToDeposit(pool, alice, true);

// Bob (not allowlisted) calls the public liquidity adder, naming alice as owner.
vm.startPrank(bob);
token0.approve(address(liquidityAdder), type(uint256).max);
token1.approve(address(liquidityAdder), type(uint256).max);

// Extension checks allowedDepositor[pool][alice] == true → passes.
// Bob pays tokens; alice receives the LP position.
liquidityAdder.addLiquidityExactShares(
    pool,
    alice,           // owner: allowlisted → guard passes
    salt,
    deltas,
    type(uint256).max,
    type(uint256).max,
    ""
);
vm.stopPrank();

// Bob (non-allowlisted) has successfully injected tokens into the pool.
assertGt(stateView.positionBinShares(pool, alice, salt, bin), 0);
// Confirmed by existing test pattern at MetricOmmPoolLiquidityAdder.t.sol:240-254
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
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
```

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L240-254)
```text
  function test_exactShares_usesMsgSenderAsPayerNotOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    uint256 aliceWethBefore = weth.balanceOf(alice);
    uint256 bobWethBefore = weth.balanceOf(bob);

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 12, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 12, int8(4));
    assertGt(bobShares, 0);
    assertLt(weth.balanceOf(alice), aliceWethBefore);
    assertEq(weth.balanceOf(bob), bobWethBefore);
  }
```
