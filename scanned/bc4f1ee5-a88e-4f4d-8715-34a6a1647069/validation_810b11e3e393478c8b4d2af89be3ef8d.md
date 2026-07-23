The key facts are now clear. Let me trace the exact path:

1. `MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` parameter as `owner` to `_beforeAddLiquidity`.
2. `DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but **silently discards `sender`** (unnamed first parameter) and checks only `allowedDepositor[msg.sender][owner]` — where `msg.sender` is the pool.
3. The pool's `addLiquidity` has **no restriction** requiring `msg.sender == owner`.
4. `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` explicitly allows any caller to supply an arbitrary `owner` (only validates `owner != address(0)`).

This means any non-allowlisted operator can call `addLiquidity(owner=allowlisted_address)` and the check passes because the allowlisted `owner` satisfies `allowedDepositor[pool][owner]`, while the actual `sender` is never examined.

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of transaction `sender`, allowing any non-allowlisted operator to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument and gates only on `owner`. Because `MetricOmmPool.addLiquidity` accepts any caller-supplied `owner` with no `msg.sender == owner` requirement, any address can bypass the allowlist by naming an allowlisted address as `owner`.

### Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter (it is unnamed) and performs its gate check against `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool calls the hook with `(msg.sender, owner, ...)`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

The pool imposes **no** `msg.sender == owner` constraint on `addLiquidity`: [3](#0-2) 

The periphery router `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` explicitly supports a caller-supplied `owner` distinct from `msg.sender`, with only a non-zero check: [4](#0-3) 

### Impact Explanation
The deposit allowlist is the pool admin's mechanism to restrict which addresses may add liquidity (e.g., for KYC/compliance or controlled launch). Because the gate checks `owner` rather than `sender`, any non-allowlisted address can:

1. Call `pool.addLiquidity(owner = allowlisted_alice, ...)` directly, or
2. Call `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner = allowlisted_alice, ...)`.

The extension check passes (`allowedDepositor[pool][alice] == true`), the non-allowlisted operator's tokens are pulled via callback, and a position is minted under Alice's key. The pool admin's deposit restriction is completely ineffective — any actor can inject liquidity into the pool by fronting tokens for an allowlisted owner. This breaks the core functionality of `DepositAllowlistExtension`.

### Likelihood Explanation
Exploitation requires only knowledge of one allowlisted address (observable on-chain via `AllowedToDepositSet` events) and the ability to call `addLiquidity`. No privileged access, no malicious setup, and no non-standard token behavior is needed. The periphery router makes the `owner != sender` path a first-class supported flow. [5](#0-4) 

### Recommendation
Change `beforeAddLiquidity` to check `sender` (the actual transaction initiator) instead of `owner`:

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

If the intent is to gate on the position owner (not the payer), the contract's NatSpec, event names (`AllowedToDepositSet`), and function names (`setAllowedToDeposit`) should be updated to reflect that, and the security model re-evaluated. [6](#0-5) 

### Proof of Concept
```solidity
// Foundry test sketch
function test_operatorBypassesAllowlist() public {
    address alice = makeAddr("alice");   // allowlisted owner
    address bob   = makeAddr("bob");    // NOT allowlisted

    // Pool admin allowlists alice only
    vm.prank(admin);
    depositExtension.setAllowedToDeposit(address(pool), alice, true);

    // Fund bob with tokens and approve pool
    deal(address(token0), bob, 1e18);
    deal(address(token1), bob, 1e18);
    vm.startPrank(bob);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // Bob calls addLiquidity naming alice as owner — check passes because alice is allowlisted
    LiquidityDelta memory d = /* bins above price */;
    pool.addLiquidity(alice, 0, d, "", "");  // succeeds despite bob not being allowlisted

    // Alice now has a position she didn't initiate; bob bypassed the gate
    assertGt(positionBinShares[alice][...], 0);
    vm.stopPrank();
}
``` [1](#0-0)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
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
