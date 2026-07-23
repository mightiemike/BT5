### Title
`DepositAllowlistExtension` Checks `owner` Instead of Payer, Allowing Any Address to Bypass Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` parameter (the LP share recipient), not the `sender` parameter (the actual token payer). Because `MetricOmmPoolLiquidityAdder` separates `owner` from `msg.sender`/payer, any non-allowlisted address can deposit funds into a curated pool by supplying an allowlisted address as `owner`.

---

### Finding Description

The full call chain is:

1. Alice (not allowlisted) calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner=bob, ...)`. [1](#0-0) 

2. The adder stores Alice as `payer` in transient context and calls `pool.addLiquidity(owner=bob, ...)`. [2](#0-1) 

3. The pool calls `_beforeAddLiquidity(sender=adder_contract, owner=bob, ...)` — `sender` is `msg.sender` of `addLiquidity`, i.e. the adder contract address, not Alice. [3](#0-2) 

4. `ExtensionCalling._beforeAddLiquidity` forwards `(sender=adder, owner=bob, ...)` to the extension. [4](#0-3) 

5. `DepositAllowlistExtension.beforeAddLiquidity` **silently discards** the `sender` parameter (first `address,` is unnamed) and checks only `owner`:

   ```solidity
   function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
       ...
   {
       if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
           revert IMetricOmmPoolActions.NotAllowedToDeposit();
       }
   ``` [5](#0-4) 

   Since `owner=bob` is allowlisted, the check passes.

6. Back in the callback, Alice's tokens are pulled as `payer`: [6](#0-5) 

Alice's tokens enter the pool; Bob receives LP shares he did not request. The allowlist is completely bypassed.

The `IMetricOmmPoolLiquidityAdder` NatSpec explicitly documents this separation: *"The position `owner` may differ from `msg.sender`, but token pulls in callback are always sourced from `msg.sender` that initiated the add call."* [7](#0-6) 

The existing test `test_exactShares_usesMsgSenderAsPayerNotOwner` confirms Alice pays while Bob receives shares — but this test runs against a pool **without** `DepositAllowlistExtension`, so the bypass is never caught. [8](#0-7) 

---

### Impact Explanation

The deposit allowlist is the primary curation mechanism for permissioned pools. The bypass means:
- Any address can deposit into a pool regardless of allowlist status, completely defeating pool curation.
- LP shares are minted to an allowlisted address without that address's consent (griefing).
- The pool admin has no effective way to restrict deposits once the adder's `owner` separation is exploited.

This is broken core pool functionality with direct fund-flow impact (non-allowlisted tokens enter the pool).

---

### Likelihood Explanation

The `addLiquidityExactShares(pool, owner, ...)` overload is a public, documented entrypoint. No special privileges are required. Any address that has approved the adder can execute this with a single transaction. The `owner` parameter is freely chosen by the caller.

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` must check `sender` (the actual depositor/payer as seen by the pool), not `owner`. Change:

```solidity
// Before (checks owner — wrong)
function beforeAddLiquidity(address, address owner, ...)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (checks sender — correct)
function beforeAddLiquidity(address sender, address, ...)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

Note: when called through `MetricOmmPoolLiquidityAdder`, `sender` will be the adder contract address, not the original EOA. A complete fix may also require the adder to pass the original `msg.sender` through `extensionData` or a dedicated field, or the allowlist to allowlist the adder contract itself and rely on a separate per-user check.

---

### Proof of Concept

```solidity
// Foundry integration test
function test_disallowedPayerBypassesAllowlistViaOwner() public {
    address alice = makeAddr("alice"); // NOT allowlisted
    address bob   = makeAddr("bob");   // allowlisted

    // Setup: bob is allowlisted, alice is not
    depositExtension.setAllowedToDeposit(address(pool), bob, true);

    // Fund and approve alice
    deal(address(token0), alice, 1_000e18);
    deal(address(token1), alice, 1_000e18);
    vm.startPrank(alice);
    token0.approve(address(adder), type(uint256).max);
    token1.approve(address(adder), type(uint256).max);

    LiquidityDelta memory d = /* single bin delta */;

    // Alice deposits with owner=bob — allowlist check passes because bob is allowed
    // Alice's tokens are pulled; bob receives LP shares
    (uint256 a0, uint256 a1) = adder.addLiquidityExactShares(
        address(pool), bob, 0, d, type(uint256).max, type(uint256).max, ""
    );
    vm.stopPrank();

    // Assert: deposit succeeded despite alice not being allowlisted
    assertGt(a0 + a1, 0, "deposit succeeded");
    // Alice's tokens were pulled
    assertLt(token0.balanceOf(alice) + token1.balanceOf(alice), 2_000e18);
    // Bob received shares without contributing tokens
    assertGt(stateView.positionBinShares(address(pool), bob, 0, binIdx), 0);
}
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L192-196)
```text
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L13-14)
```text
/// @dev The position `owner` may differ from `msg.sender`, but token pulls in callback are always sourced from
///      `msg.sender` that initiated the add call.
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
