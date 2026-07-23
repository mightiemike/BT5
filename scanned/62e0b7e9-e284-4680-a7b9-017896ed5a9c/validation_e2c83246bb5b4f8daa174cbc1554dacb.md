Let me look at how the pool passes `sender` and `owner` to the extension, and confirm the `BaseMetricExtension.onlyPool` modifier.

The full call chain is now confirmed. Here is the complete analysis:

---

### Title
`DepositAllowlistExtension` checks LP-share recipient (`owner`) instead of the actual token payer, allowing any non-allowlisted address to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking `allowedDepositor[pool][owner]`, where `owner` is the LP-share recipient. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly allows `owner ≠ msg.sender`, any non-allowlisted payer can route a deposit through an allowlisted owner address, bypassing the gate entirely.

---

### Finding Description

**Call chain:**

1. Attacker (non-allowlisted) calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedOwner, salt, deltas, max0, max1, extensionData)`. [1](#0-0) 

2. The adder stores `msg.sender` (attacker) as the payer in transient context, then calls `pool.addLiquidity(positionOwner=allowlistedOwner, ...)`. [2](#0-1) 

3. The pool calls `_beforeAddLiquidity(msg.sender=LiquidityAdder, owner=allowlistedOwner, ...)`. [3](#0-2) 

4. `ExtensionCalling._beforeAddLiquidity` forwards `(sender=LiquidityAdder, owner=allowlistedOwner, ...)` to the extension. [4](#0-3) 

5. `DepositAllowlistExtension.beforeAddLiquidity` evaluates:
   ```solidity
   if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
       revert IMetricOmmPoolActions.NotAllowedToDeposit();
   }
   ```
   Here `msg.sender` = pool, `owner` = allowlisted address → **check passes**. [5](#0-4) 

6. The pool proceeds; the callback pulls tokens from the **attacker** (payer) and credits LP shares to the **allowlisted owner**. [6](#0-5) 

**Root cause:** The actual payer (`msg.sender` of `addLiquidityExactShares`) is never passed to the extension. The `sender` parameter in `beforeAddLiquidity` is the `MetricOmmPoolLiquidityAdder` contract address, not the originating caller. The extension has no way to check the real depositor — it can only see `owner` (LP recipient) or `sender` (router address), and it chose `owner`.

The contract's own NatSpec states the intent: *"Gates `addLiquidity` by depositor address, per pool"* — but the implementation gates by LP-share recipient, not depositor. [7](#0-6) 

---

### Impact Explanation

The deposit allowlist is completely ineffective. Any non-allowlisted address can deposit into a restricted pool by specifying any allowlisted address as `owner`. The pool admin's access control — intended for compliance, KYC gating, or institutional whitelisting — is bypassed by an unprivileged public path with no special preconditions. This is an admin-boundary break: a pool admin restriction is circumvented by an unprivileged caller.

---

### Likelihood Explanation

The exploit path is trivially accessible: call `addLiquidityExactShares` with any allowlisted address as `owner`. No privileged access, no special setup, no oracle manipulation. The allowlisted owner list is public on-chain (`allowedDepositor` is a public mapping), so any attacker can enumerate valid owner addresses.

---

### Recommendation

The extension must gate on the actual token payer, not the LP-share recipient. Since the payer is not currently forwarded through the hook interface, the fix requires one of:

1. **Pass the payer through `extensionData`**: The `MetricOmmPoolLiquidityAdder` encodes the payer address into `extensionData`, and `DepositAllowlistExtension` decodes and checks it. This requires a convention between the adder and the extension.

2. **Check `sender` instead of `owner`**: If the pool is only accessed directly (no router), `sender` equals the depositor. But this breaks when a router is used, since `sender` becomes the router address.

3. **Require `owner == sender` in the extension**: Reject any deposit where the LP recipient differs from the caller. This is the simplest safe default for a deposit allowlist.

The cleanest production fix is option 3 — add `require(sender == owner)` in `beforeAddLiquidity` — combined with checking `allowedDepositor[msg.sender][sender]`. This ensures the allowlisted party is both the payer and the LP recipient.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_nonAllowlistedPayerBypassesDepositGate() public {
    // Setup: pool with DepositAllowlistExtension, only `bob` is allowlisted
    address alice = makeAddr("alice"); // NOT allowlisted
    address bob   = makeAddr("bob");   // allowlisted

    vm.prank(poolAdmin);
    extension.setAllowedToDeposit(address(pool), bob, true);
    // alice is NOT set

    // Fund alice and approve the adder
    token0.mint(alice, 1_000e18);
    vm.prank(alice);
    token0.approve(address(liquidityAdder), type(uint256).max);

    // Alice deposits, specifying bob as owner — gate should block alice but doesn't
    vm.prank(alice);
    (uint256 a0,) = liquidityAdder.addLiquidityExactShares(
        address(pool), bob, 1, delta, type(uint256).max, type(uint256).max, ""
    );

    // Assert: deposit succeeded (alice's tokens pulled, bob got LP shares)
    assertGt(a0, 0); // alice's tokens were taken
    assertGt(stateView.positionBinShares(address(pool), bob, 1, binIdx), 0); // bob got shares
    // The deposit gate was bypassed — alice (non-allowlisted) deposited successfully
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
