The call trace is fully traceable. Here is the determination:

---

### Title
`DepositAllowlistExtension` Checks `owner` Instead of Actual Depositor, Allowing Any User to Bypass Pool Deposit Allowlist via `MetricOmmPoolLiquidityAdder` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates deposits by checking whether the `owner` (position recipient) is allowlisted, but ignores the `sender` parameter entirely. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lets any caller freely specify an arbitrary `owner`, a non-allowlisted user can bypass the deposit allowlist by naming an allowlisted EOA as `owner`. The actual payer (the attacker) is never checked.

---

### Finding Description

**Step 1 — Attacker entry point:**

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, salt, deltas, max0, max1, extensionData)` accepts a caller-controlled `owner`. The only validation is `_validateOwner(owner)`, which only rejects `address(0)`. [1](#0-0) [2](#0-1) 

**Step 2 — Pool receives `msg.sender` = adder, `owner` = attacker-supplied:**

`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)` where `msg.sender` is the adder contract address and `owner` is the attacker-supplied allowlisted EOA. [3](#0-2) 

**Step 3 — Extension receives both, but only checks `owner`:**

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (unnamed `address,`) and checks only `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool and `owner` is the attacker-supplied allowlisted EOA. The check passes. [4](#0-3) 

**Step 4 — Tokens pulled from attacker, position credited to allowlisted EOA:**

In the callback, `payer` is `msg.sender` of the original adder call (the attacker). The attacker's tokens are pulled; the position is minted under the allowlisted EOA's key. [5](#0-4) 

---

### Impact Explanation

The `DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." Its invariant — that only allowlisted addresses may deposit — is completely broken when `MetricOmmPoolLiquidityAdder` is used. Any non-allowlisted user can deposit into a curated pool by specifying any allowlisted EOA as `owner`. The pool admin's curation control is rendered ineffective. This is broken core pool functionality. [6](#0-5) 

---

### Likelihood Explanation

The attack requires no special privileges. `MetricOmmPoolLiquidityAdder` is a public periphery contract. Any user who knows one allowlisted address (which is on-chain readable via `allowedDepositor`) can execute the bypass. The `addLiquidityExactShares` overload with an explicit `owner` parameter is a standard, documented entry point. [7](#0-6) 

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` should check the `sender` (the actual depositing entity / `msg.sender` of the pool's `addLiquidity` call) rather than — or in addition to — `owner`. The corrected guard:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender]   // check actual depositor
        && !allowedDepositor[msg.sender][owner]) { // optionally also allow owner
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The exact policy (sender-only, owner-only, or both) should match the pool admin's intent, but at minimum `sender` must be checked.

---

### Proof of Concept

```solidity
// Foundry integration test
function test_allowlistBypass() public {
    address allowlisted = makeAddr("allowlisted");
    address attacker    = makeAddr("attacker");

    // Pool admin allowlists only `allowlisted`
    depositExtension.setAllowedToDeposit(address(pool), allowlisted, true);

    // Fund attacker and approve adder
    token0.mint(attacker, 1_000_000e18);
    token1.mint(attacker, 1_000_000e18);
    vm.startPrank(attacker);
    token0.approve(address(adder), type(uint256).max);
    token1.approve(address(adder), type(uint256).max);

    LiquidityDelta memory d = /* some valid delta */;

    // Attacker is NOT allowlisted, but specifies allowlisted as owner
    // This should revert but does NOT
    adder.addLiquidityExactShares(
        address(pool),
        allowlisted,   // owner = allowlisted EOA
        1,
        d,
        type(uint256).max,
        type(uint256).max,
        ""
    );
    vm.stopPrank();

    // Position was created; attacker's tokens were spent; allowlist bypassed
    uint256 shares = pool.positionBinShares(allowlisted, 1, binIdx);
    assertGt(shares, 0); // bypass confirmed
}
``` [4](#0-3) [1](#0-0)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L12-14)
```text
/// @notice Periphery contract for adding liquidity with caller-funded token settlement.
/// @dev The position `owner` may differ from `msg.sender`, but token pulls in callback are always sourced from
///      `msg.sender` that initiated the add call.
```
