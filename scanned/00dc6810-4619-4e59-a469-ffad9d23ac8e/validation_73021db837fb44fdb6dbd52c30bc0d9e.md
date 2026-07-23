### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is documented as "Gates `addLiquidity` by depositor address, per pool." However, the hook silently ignores the `sender` argument (the actual initiator of the pool call) and instead validates only the `owner` argument (the LP position recipient). Because `owner` is a free caller-supplied parameter with no binding to `msg.sender`, any unprivileged address can bypass the allowlist by nominating an already-allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both values verbatim: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the `sender` parameter is unnamed and discarded; only `owner` is checked: [3](#0-2) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the two-argument overload with an explicit `owner`) accepts any non-zero `owner` address from the caller, stores `msg.sender` as the payer in transient storage, and forwards the caller-supplied `owner` directly to the pool: [4](#0-3) 

The only validation on `owner` is a zero-address check: [5](#0-4) 

**Attack path:**

1. Pool is configured with `DepositAllowlistExtension`; Alice (`0xAlice`) is allowlisted, Bob (`0xBob`) is not.
2. Bob calls `LiquidityAdder.addLiquidityExactShares(pool, 0xAlice, salt, deltas, max0, max1, extData)`.
3. LiquidityAdder calls `pool.addLiquidity(0xAlice, salt, deltas, abi.encode(KIND_PAY), extData)`.
4. Pool calls `_beforeAddLiquidity(LiquidityAdder, 0xAlice, ...)`.
5. Extension evaluates `allowedDepositor[pool][0xAlice]` → `true` → hook passes.
6. Bob's tokens are pulled from Bob's wallet; the LP position is minted to Alice.

The allowlist guard is fully bypassed. Bob (the actual depositor and payer) is never checked.

Note the structural contrast with `SwapAllowlistExtension`, which correctly checks `sender` (the actual swap initiator): [6](#0-5) 

---

### Impact Explanation

The pool admin deploys `DepositAllowlistExtension` to enforce a closed LP set — e.g., for regulatory compliance, to prevent wash-trading, or to maintain pool composition. Any unprivileged address can circumvent this control by nominating any allowlisted address as `owner`. Unauthorized liquidity enters the pool, diluting existing LP positions and invalidating the admin's access-control invariant. This is a direct admin-boundary break: a pool-admin-configured guard is bypassed by an unprivileged path with no special permissions required.

---

### Likelihood Explanation

The `MetricOmmPoolLiquidityAdder` is the standard periphery entry point for EOA deposits and is publicly deployed. The bypass requires only knowing one allowlisted address (readable from `allowedDepositor` mapping, which is `public`) and calling the standard `addLiquidityExactShares` overload with that address as `owner`. No privileged access, flash loans, or oracle manipulation is needed.

---

### Recommendation

Check `sender` (the actual pool-call initiator) in addition to — or instead of — `owner`. Because `sender` equals the LiquidityAdder contract when the periphery is used, the allowlist should either:

1. **Check `sender`** and require integrators (LiquidityAdder, etc.) to be individually allowlisted, giving the admin coarse-grained control over entry points; or
2. **Check both `sender` and `owner`**, requiring both to be allowlisted; or
3. **Document explicitly** that the allowlist gates position ownership (not payment origin) and rename the mapping/events accordingly so pool admins are not misled.

The minimal code fix in `DepositAllowlistExtension.beforeAddLiquidity`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (
        !allowAllDepositors[msg.sender] &&
        !allowedDepositor[msg.sender][sender] &&   // ← add sender check
        !allowedDepositor[msg.sender][owner]
    ) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Assume:
//   pool          — MetricOmmPool with DepositAllowlistExtension configured
//   allowlist     — DepositAllowlistExtension instance
//   adder         — MetricOmmPoolLiquidityAdder instance
//   alice         — allowlisted address (allowedDepositor[pool][alice] == true)
//   bob           — NOT allowlisted; holds token0/token1 and has approved `adder`

// Bob bypasses the deposit allowlist:
vm.startPrank(bob);
token0.approve(address(adder), type(uint256).max);
token1.approve(address(adder), type(uint256).max);

// bob is not allowlisted, but alice is — use alice as owner
adder.addLiquidityExactShares(
    pool,
    alice,          // owner: allowlisted → hook passes
    0,              // salt
    deltas,
    max0,
    max1,
    ""
);
vm.stopPrank();

// Result: bob's tokens are in the pool; alice holds the LP position.
// The DepositAllowlistExtension never checked bob.
assertEq(pool.positionShares(alice, 0, binIdx), expectedShares);
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
