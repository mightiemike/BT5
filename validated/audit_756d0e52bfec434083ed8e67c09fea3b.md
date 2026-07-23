The attack path is fully traceable. Let me confirm the exact check in the extension and the pool's `addLiquidity` sender binding.

The attack path is fully confirmed by the code. Here is the complete analysis:

---

### Title
Deposit Allowlist Bypass via Attacker-Controlled `owner` Parameter — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking `allowedDepositor[pool][owner]`, where `owner` is the LP position recipient — not the address actually paying tokens. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` freely accepts any non-zero `owner` from the caller while always sourcing tokens from `msg.sender` (the payer), an attacker who is not allowlisted can deposit into an allowlist-gated pool by supplying an allowlisted victim address as `owner`.

### Finding Description

**Step 1 — Public entry point, no restriction on `owner`:**

`addLiquidityExactShares(pool, owner, salt, deltas, max0, max1, extensionData)` accepts any `owner != address(0)`. The only validation is `_validateOwner`, which is a zero-address check. [1](#0-0) [2](#0-1) 

**Step 2 — Payer is always `msg.sender`, stored in transient context:**

`_addLiquidity` stores `payer = msg.sender` (the attacker) in transient storage, then calls `pool.addLiquidity(positionOwner=victim, ...)`. The payer and the owner are fully decoupled. [3](#0-2) 

**Step 3 — Pool forwards `owner=victim` to the extension hook:**

The pool calls `_beforeAddLiquidity(sender, owner, ...)` which encodes `owner` (victim) into the extension call. The `sender` here is the adder contract address, not the actual user. [4](#0-3) 

**Step 4 — Extension checks `owner`, not the payer:**

`beforeAddLiquidity` ignores the `sender` parameter entirely (unnamed `address`) and checks only `allowedDepositor[msg.sender][owner]`, where `msg.sender` is the pool and `owner` is the attacker-supplied victim address. [5](#0-4) 

Since `allowedDepositor[pool][victim] = true`, the check passes. LP shares are minted to `victim`. The callback then pulls tokens from the attacker (payer). [6](#0-5) 

The existing test suite explicitly confirms this payer/owner split is functional: [7](#0-6) 

### Impact Explanation

The `DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. The invariant is that only allowlisted addresses may deposit. This invariant is broken: any address can deposit into an allowlist-gated pool by routing through an allowlisted `owner`. Pool curation is defeated. The attacker spends their own tokens; the victim receives unwanted LP shares (which they can remove, but the unauthorized deposit into the pool has already occurred). The severity is **Medium** — broken core pool functionality (allowlist bypass) without direct principal loss to the victim or protocol, since the attacker self-funds the deposit.

### Likelihood Explanation

The attack requires only a public call to `addLiquidityExactShares` with `owner` set to any allowlisted address. No privileged access, no malicious pool, no oracle manipulation. Any address can execute this against any pool using `DepositAllowlistExtension` with at least one allowlisted depositor.

### Recommendation

The extension must gate on the **actual token payer**, not the LP position owner. The `sender` parameter passed to `beforeAddLiquidity` is the adder contract, not the user, so it cannot be used directly. Two viable fixes:

1. **Check `sender` instead of `owner`** — if the protocol guarantees that `sender` is always the actual user (requires router-level enforcement that `sender == msg.sender` of the original call).
2. **Require `owner == sender`** in `beforeAddLiquidity` — disallow the operator pattern for allowlist-gated pools, so the LP recipient must be the same address as the caller.
3. **Pass payer explicitly** — extend the hook interface or `extensionData` to carry the verified payer address from the router.

### Proof of Concept

```solidity
// Setup: pool uses DepositAllowlistExtension
// allowedDepositor[pool][victim] = true
// allowedDepositor[pool][attacker] = false

vm.prank(attacker);
// attacker pays, victim receives LP shares — allowlist bypassed
adder.addLiquidityExactShares(
    pool,
    victim,   // owner = allowlisted address
    salt,
    deltas,
    type(uint256).max,
    type(uint256).max,
    ""
);

// Assert: victim has LP shares, attacker's tokens were consumed, allowlist did not block
uint256 victimShares = stateView.positionBinShares(pool, victim, salt, binIdx);
assertGt(victimShares, 0);
assertLt(token0.balanceOf(attacker), attackerToken0Before);
```

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-68)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-196)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L94-99)
```text
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
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
