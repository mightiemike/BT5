The key code is already found. Let me trace the exact exploit path:

The exploit path is fully traceable. Here is the complete analysis:

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of the Actual Token Payer, Allowing Any Non-Allowlisted Address to Inject Liquidity into a Restricted Pool — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking the `owner` (position recipient) parameter against the allowlist. However, `MetricOmmPoolLiquidityAdder` separates the position owner from the token payer: `owner` is caller-supplied, while the actual payer is `msg.sender` stored in transient storage. An attacker who is not allowlisted can call `addLiquidityExactShares(pool, owner=allowlisted_user, ...)`, pass the allowlist check (because `allowlisted_user` is allowlisted), and have their own tokens pulled into the restricted pool.

### Finding Description

**Call chain:**

1. Attacker calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner=allowlisted_user, ...)`. [1](#0-0) 

2. The adder stores `payer = msg.sender` (attacker) in transient storage and calls `pool.addLiquidity(positionOwner=allowlisted_user, ...)`. [2](#0-1) 

3. The pool calls `_beforeAddLiquidity(sender=LiquidityAdder, owner=allowlisted_user, ...)`, which dispatches to `DepositAllowlistExtension.beforeAddLiquidity`. [3](#0-2) 

4. The extension checks `allowedDepositor[msg.sender][owner]` — i.e., `allowedDepositor[pool][allowlisted_user]`. Since `allowlisted_user` is allowlisted, the check **passes**. The `sender` parameter (LiquidityAdder) is silently discarded; the actual payer (attacker) is never visible to the extension. [4](#0-3) 

5. The pool mints shares to `allowlisted_user` and calls back the LiquidityAdder, which pulls tokens from the attacker (payer). [5](#0-4) 

**Root cause:** The extension's `beforeAddLiquidity` signature receives `sender` (first argument, unnamed and discarded) and `owner`. It checks `owner` against the allowlist. The actual token payer is the LiquidityAdder's transient `payer` slot, which is never forwarded to the extension. The pool's own NatSpec even documents the operator pattern explicitly: *"`msg.sender` pays but need not equal `owner`"*. [6](#0-5) 

### Impact Explanation

The deposit allowlist is the pool admin's mechanism to restrict which addresses may provide liquidity (e.g., for KYC/AML compliance or controlled liquidity programs). Any non-allowlisted address can bypass it entirely by supplying any allowlisted address as `owner`. The attacker's tokens enter the restricted pool; the position is credited to the allowlisted user. The admin-set access control is rendered ineffective for all pools using `DepositAllowlistExtension`. This is an admin-boundary break via an unprivileged public path.

### Likelihood Explanation

The `addLiquidityExactShares(pool, owner, ...)` overload is a public, permissionless function. No special role or setup is required beyond knowing one allowlisted address (which is on-chain readable via `allowedDepositor`). The bypass is deterministic and requires no flash loan, oracle manipulation, or race condition.

### Recommendation

`beforeAddLiquidity` must gate the economically relevant actor — the token payer — not the position recipient. Two options:

1. **Check `sender` instead of `owner`**: The pool passes `msg.sender` of `addLiquidity` as `sender`. When called through the LiquidityAdder, `sender` is the LiquidityAdder address. This still does not expose the real payer, so the LiquidityAdder would need to pass the actual payer address in `extensionData` and the extension would decode it.

2. **Require `owner == sender` in the extension**: Disallow the operator pattern for allowlisted pools, so only the position owner can deposit on their own behalf.

The cleanest fix is option 1: the LiquidityAdder encodes the real payer in `extensionData`, and `DepositAllowlistExtension` decodes and checks it when present.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistBypass() public {
    // Setup: pool with DepositAllowlistExtension, only allowlisted_user is allowed
    address attacker = makeAddr("attacker");
    address allowlisted_user = makeAddr("allowlisted_user");
    extension.setAllowedToDeposit(address(pool), allowlisted_user, true);

    // Fund attacker and approve LiquidityAdder
    deal(token0, attacker, 1_000e18);
    vm.prank(attacker);
    IERC20(token0).approve(address(liquidityAdder), type(uint256).max);

    uint256 attackerBalBefore = IERC20(token0).balanceOf(attacker);

    // Attacker deposits with owner=allowlisted_user — passes allowlist check
    vm.prank(attacker);
    liquidityAdder.addLiquidityExactShares(
        address(pool),
        allowlisted_user, // owner = allowlisted, attacker is payer
        0,
        delta,
        type(uint256).max,
        type(uint256).max,
        ""
    );

    // Attacker's tokens were pulled despite attacker not being allowlisted
    assertLt(IERC20(token0).balanceOf(attacker), attackerBalBefore);
    // Position credited to allowlisted_user, not attacker
    assertGt(stateView.positionBinShares(address(pool), allowlisted_user, 0, binIdx), 0);
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
```
