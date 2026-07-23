### Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` (position holder) instead of the actual payer, allowing any non-allowlisted actor to bypass the deposit guard via `MetricOmmPoolLiquidityAdder` - (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual economic actor / token payer) and instead checks only the `owner` argument (the position holder) against the allowlist. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any `msg.sender` specify an arbitrary `owner`, a non-allowlisted payer can route through an allowlisted `owner` address to pass the guard and inject tokens into the pool.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is defined as:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first parameter (`sender`) is silently discarded. The guard checks `allowedDepositor[pool][owner]`. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` passes both `sender` (the pool's `msg.sender`, i.e. the liquidity adder contract) and `owner` (the position holder) to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the owner-specifying overload) allows any `msg.sender` to name an arbitrary `owner`, with only a non-zero check (`_validateOwner`):

```solidity
function addLiquidityExactShares(
    address pool, address owner, uint80 salt, LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1, bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
}
``` [3](#0-2) 

The payer (`msg.sender` of the liquidity adder) and the position holder (`owner`) are deliberately separated. The allowlist guard sees only `owner`, so a non-allowlisted payer who names an allowlisted `owner` passes the check unconditionally.

---

### Impact Explanation

The `DepositAllowlistExtension` is the pool admin's primary mechanism to restrict who can economically participate in the pool (inject tokens, grow LP positions). By routing through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with an allowlisted `owner`, any non-allowlisted actor can:

1. Inject arbitrary token amounts into the pool, bypassing the admin-configured deposit gate entirely.
2. Cause the allowlisted `owner` to receive an LP position they did not initiate — which can be used as a griefing vector (e.g., forcing a position on an address that has downstream accounting or tax implications).
3. Undermine any compliance, KYC, or curated-LP invariant the pool admin intended to enforce.

This is a direct admin-boundary break: an unprivileged path (`MetricOmmPoolLiquidityAdder`) circumvents a configured pool guard.

---

### Likelihood Explanation

- The `MetricOmmPoolLiquidityAdder` is a public, permissionless periphery contract.
- The attacker needs only to know one allowlisted address (trivially observable on-chain from `AllowedToDepositSet` events or `allowedDepositor` storage reads).
- No special privileges, flash loans, or oracle manipulation are required.
- The attack is repeatable for any pool that uses `DepositAllowlistExtension`.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to gate on `sender` (the actual payer / caller of the pool) rather than `owner` (the position holder):

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

If the intent is to gate both the payer and the position holder, check both. Additionally, document clearly which identity the allowlist is intended to restrict so that pool admins configure it correctly.

---

### Proof of Concept

```solidity
// Pool is deployed with DepositAllowlistExtension.
// Admin allowlists only `alice`.
extension.setAllowedToDeposit(pool, alice, true);

// Bob (not allowlisted) calls the public liquidity adder,
// naming alice as owner. Bob is the payer (msg.sender).
vm.startPrank(bob);
token0.approve(address(liquidityAdder), type(uint256).max);
token1.approve(address(liquidityAdder), type(uint256).max);

// Extension checks allowedDepositor[pool][alice] == true → passes.
// Bob pays tokens; alice receives the LP position.
liquidityAdder.addLiquidityExactShares(
    pool,
    alice,          // owner: allowlisted → guard passes
    salt,
    deltas,
    type(uint256).max,
    type(uint256).max,
    ""
);
vm.stopPrank();

// Bob (non-allowlisted) has successfully injected tokens into the pool.
// The DepositAllowlistExtension guard was bypassed.
assertGt(stateView.positionBinShares(pool, alice, salt, bin), 0);
``` [1](#0-0) [3](#0-2) [2](#0-1)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L61-68)
```text
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
