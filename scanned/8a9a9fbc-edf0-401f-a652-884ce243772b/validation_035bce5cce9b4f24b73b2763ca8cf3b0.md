### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing unprivileged callers to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates on `owner` instead. Because `MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` that is independent of `msg.sender`, any unprivileged address can deposit into a permissioned pool by naming an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is declared with the `sender` parameter unnamed (discarded):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
```

The guard reads:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
``` [1](#0-0) 

`msg.sender` here is the pool (the extension is called by the pool), so the check resolves to `allowedDepositor[pool][owner]`. The actual caller of `addLiquidity` — `sender` — is never consulted.

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the user-supplied `owner` argument as `owner`:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

There is no constraint that `owner == msg.sender` in `addLiquidity` (unlike `removeLiquidity`, which enforces `msg.sender == owner`): [3](#0-2) 

Therefore, an unprivileged caller sets `owner = allowedAddress` (any allowlisted address), the extension check passes, the pool invokes `metricOmmModifyLiquidityCallback` on the unprivileged caller to pull tokens, and LP shares are minted to `allowedAddress`.

---

### Impact Explanation

The `DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." [4](#0-3) 

The invariant — only allowlisted actors may deposit — is broken. Any address can deposit into a pool that uses this extension, bypassing the pool admin's access control entirely. Consequences include:

- **Broken core functionality**: the permissioning mechanism is ineffective; the pool is not actually restricted.
- **Griefing**: an unprivileged actor can force LP shares onto an allowlisted owner who did not initiate the deposit, creating unwanted positions under that owner's key.
- **Pool composition manipulation**: an attacker can alter the pool's liquidity distribution (bin depths, total shares) without being allowlisted, affecting swap pricing and fee accrual for existing LPs.

---

### Likelihood Explanation

The attack requires only a direct call to `pool.addLiquidity` with `owner` set to any known allowlisted address. No privileged access, oracle manipulation, or special token behavior is needed. The allowlisted address is typically discoverable on-chain via `AllowedToDepositSet` events. Likelihood is high.

---

### Recommendation

Change the guard to check `sender` (the first, currently discarded parameter) instead of `owner`:

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

This aligns with the contract's stated purpose ("gates by depositor address") and mirrors how `SwapAllowlistExtension` should gate by the actual initiating address.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_depositAllowlist_bypass_via_owner() public {
    address allowedOwner  = makeAddr("allowedOwner");
    address unprivileged  = makeAddr("unprivileged");

    // Pool admin allowlists only allowedOwner
    vm.prank(poolAdmin);
    depositExtension.setAllowedToDeposit(address(pool), allowedOwner, true);

    // Confirm unprivileged is NOT allowlisted
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), unprivileged));

    // Fund unprivileged and approve pool tokens
    deal(address(token0), unprivileged, 1e18);
    deal(address(token1), unprivileged, 1e18);
    vm.startPrank(unprivileged);
    token0.approve(address(liquidityAdder), type(uint256).max);
    token1.approve(address(liquidityAdder), type(uint256).max);

    // Unprivileged calls addLiquidity with owner = allowedOwner
    // Extension checks allowedDepositor[pool][allowedOwner] == true → passes
    // Tokens pulled from unprivileged, LP shares minted to allowedOwner
    liquidityAdder.addLiquidityExactShares(
        address(pool),
        allowedOwner,   // <-- allowlisted owner, not the caller
        salt,
        deltas,
        1e18, 1e18,
        ""
    );
    vm.stopPrank();

    // Assert: LP shares minted to allowedOwner despite unprivileged being the depositor
    assertGt(_getPositionBinShares(allowedOwner, salt, bin), 0);
}
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-195)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```
