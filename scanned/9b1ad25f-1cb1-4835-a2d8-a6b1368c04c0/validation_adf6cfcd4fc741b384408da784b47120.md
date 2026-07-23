The call path is fully traceable in the scoped code. Here is the complete analysis:

**Step 1 — `DepositAllowlistExtension.beforeAddLiquidity` checks `owner`, not `sender`** [1](#0-0) 

The `sender` parameter (first arg) is unnamed and entirely ignored. The guard is `allowedDepositor[msg.sender][owner]` — it validates the position recipient, not the token provider.

**Step 2 — `MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`** [2](#0-1) 

So when the adder calls the pool, `sender = adder address`, `owner = whatever the adder was told`.

**Step 3 — `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-specified `owner` and uses `msg.sender` as the payer** [3](#0-2) 

Bob calls `adder.addLiquidityExactShares(pool, alice, ...)`. The adder stores `payer = Bob (msg.sender)` in transient context and calls `pool.addLiquidity(owner=alice, ...)`.

**Step 4 — Token pull in callback uses the stored payer (Bob), not `owner` (alice)** [4](#0-3) 

Tokens are pulled from Bob. Alice receives the LP position.

**Step 5 — The extension guard passes because alice is allowlisted**

`allowedDepositor[pool][alice] == true` → guard passes → Bob's tokens enter the pool, alice gets shares, Bob is not checked at all.

---

The bypass is real and requires no trusted role, no malicious pool, and no non-standard token. The `sender` parameter carrying the actual token provider's identity is present in the hook signature but discarded.

---

### Title
`DepositAllowlistExtension` checks position `owner` instead of `sender`, allowing non-allowlisted addresses to deposit tokens into restricted pools — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` argument (position recipient) rather than the `sender` argument (the address that called `pool.addLiquidity` and is responsible for token settlement). Because `MetricOmmPoolLiquidityAdder` supports an operator pattern where `msg.sender ≠ owner`, any non-allowlisted address can deposit tokens into a restricted pool by specifying an allowlisted address as `owner`.

### Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` receives two identity parameters: `sender` (the direct caller of `pool.addLiquidity`, i.e., the token provider) and `owner` (the position recipient). The function silently discards `sender` and gates on `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` explicitly supports a caller-specified `owner` distinct from `msg.sender`. The adder stores `payer = msg.sender` in transient context and calls `pool.addLiquidity(owner, ...)`. The pool passes `sender = adder` and `owner = alice` to the extension. The extension checks `allowedDepositor[pool][alice]` — which is `true` — and allows the call. Bob's tokens are then pulled from the transient payer context.

### Impact Explanation
The `DepositAllowlistExtension` is the sole mechanism for restricting who may deposit tokens into a pool. The bypass allows any non-allowlisted address to inject tokens into a restricted pool (e.g., a KYC-gated or compliance-restricted pool) by routing through the adder with an allowlisted address as `owner`. The pool admin's access control invariant is broken: the pool receives tokens from principals it explicitly excluded. Additionally, the allowlisted `owner` receives an LP position they did not initiate, which may have downstream consequences (e.g., tax, compliance, or protocol-level accounting).

### Likelihood Explanation
The `MetricOmmPoolLiquidityAdder` is a public, permissionless periphery contract. The owner-override overload (`addLiquidityExactShares(pool, owner, ...)`) is a documented, intentional feature. Any actor who knows an allowlisted address can execute this path with zero privileged access.

### Recommendation
In `beforeAddLiquidity`, check `sender` (the actual token provider / pool caller) rather than `owner`:

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

If the intended semantic is "only allowlisted addresses may own positions in this pool," then both `sender` and `owner` should be checked, or the NatSpec and admin tooling should be updated to reflect that the allowlist gates position ownership, not token provision.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_nonAllowlistedCanDepositViaAdder() public {
    // Setup: pool with DepositAllowlistExtension, only alice is allowlisted
    extension.setAllowedToDeposit(address(pool), alice, true);
    // assertFalse: bob is NOT allowlisted
    assertFalse(extension.isAllowedToDeposit(address(pool), bob));

    // Bob approves the adder and calls with owner=alice
    vm.startPrank(bob);
    token0.approve(address(adder), type(uint256).max);
    token1.approve(address(adder), type(uint256).max);
    // This should revert if the allowlist worked correctly, but it succeeds:
    adder.addLiquidityExactShares(address(pool), alice, salt, deltas, max0, max1, "");
    vm.stopPrank();

    // Bob's tokens were consumed, alice has LP shares
    assertGt(stateView.positionBinShares(address(pool), alice, salt, binIdx), 0);
    assertLt(token0.balanceOf(bob), initialBobBalance0); // Bob paid
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
