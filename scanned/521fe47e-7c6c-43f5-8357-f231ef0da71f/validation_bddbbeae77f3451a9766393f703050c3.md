The vulnerability is real. Here is the full analysis:

---

### Title
Deposit Allowlist Bypass via Owner/Sender Mismatch — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` (LP position recipient) against the allowlist instead of the `sender` (actual depositor). Any non-allowlisted address can bypass the deposit gate by specifying an allowlisted address as `owner`, paying tokens themselves, and minting LP shares to that address.

### Finding Description

In `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and silently discarded. The guard only checks `allowedDepositor[msg.sender][owner]`, where `msg.sender` is the pool and `owner` is the LP position recipient: [1](#0-0) 

The `sender` argument — which is the `msg.sender` of the pool's `addLiquidity` call — is completely ignored: [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly allows a caller to specify an arbitrary `owner` distinct from themselves, and stores `msg.sender` as the payer in transient context: [3](#0-2) 

The callback then pulls tokens from the stored `payer` (the actual depositor, `addressB`), not from `owner`: [4](#0-3) 

The existing test `test_exactShares_usesMsgSenderAsPayerNotOwner` explicitly confirms that `alice` (payer) can deposit tokens while `bob` (owner) receives LP shares — this is the exact separation the allowlist fails to gate: [5](#0-4) 

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism to restrict who can add liquidity. Because the guard checks `owner` (LP recipient) rather than the actual depositor, the restriction is completely non-functional when `owner != payer`. Any non-allowlisted address can deposit tokens into a restricted pool by nominating any allowlisted address as `owner`. The pool receives tokens from unauthorized depositors, breaking the core access control invariant the extension is designed to enforce.

### Likelihood Explanation

The attack path is fully public and requires no privileged access: `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` is callable by anyone. The only prerequisite is knowing one allowlisted address, which is readable from `allowedDepositor` (a public mapping). The adder itself documents and tests the owner/payer split as an intentional feature.

### Recommendation

The `beforeAddLiquidity` hook must check the actual depositing identity, not the LP recipient. The `sender` parameter (first argument) is the `msg.sender` of the pool's `addLiquidity` call. For direct EOA→pool calls this equals the depositor; for adder-mediated calls it equals the adder. The extension should check `sender` rather than `owner`. For adder-mediated flows, the adder should propagate the real payer identity (e.g., via `extensionData`) so the extension can gate the economically relevant actor.

### Proof of Concept

```solidity
// addressA is allowlisted; addressB is not.
depositExtension.setAllowedToDeposit(address(pool), addressA, true);

// addressB calls the adder with owner = addressA
vm.prank(addressB);
adder.addLiquidityExactShares(
    address(pool),
    addressA,   // owner — allowlisted, passes the guard
    salt,
    deltas,
    type(uint256).max,
    type(uint256).max,
    ""
);

// Result: addressB's tokens were pulled (payer = msg.sender = addressB)
// addressA received LP shares
// The allowlist was completely bypassed
assertGt(pool.positionShares(addressA, salt, bin), 0);
assertLt(token0.balanceOf(addressB), initialBalance); // addressB paid
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
