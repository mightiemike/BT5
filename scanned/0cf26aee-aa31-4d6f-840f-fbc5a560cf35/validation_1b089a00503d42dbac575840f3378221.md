Based on my research into the Metric OMM codebase, I found a concrete analog in the extension guard system.

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged actor to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook silently drops the `sender` parameter (the actual caller who pays for and initiates the deposit) and gates only on `owner` (the position recipient). Because `addLiquidity` accepts an arbitrary `owner` distinct from `msg.sender`, any actor not on the allowlist can bypass the guard by naming an allowed address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook: `sender = msg.sender` (the actual depositor who will be charged via the liquidity callback) and `owner` (the address that receives the position shares). The pool calls:

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData)
``` [1](#0-0) 

The extension hook signature receives both:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
``` [2](#0-1) 

But `DepositAllowlistExtension` discards `sender` entirely (unnamed first parameter) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

The `MetricOmmPoolLiquidityAdder` explicitly supports adding liquidity on behalf of a different `owner` than `msg.sender`:

```solidity
function addLiquidityExactShares(
    address pool, address owner, uint80 salt, LiquidityDelta calldata deltas, ...
) external payable returns (uint256 amount0Added, uint256 amount1Added);
``` [4](#0-3) 

This is confirmed by the test suite, which explicitly verifies that `alice` (sender) can add liquidity on behalf of `bob` (owner): [5](#0-4) 

**Attack path:**

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only address `A` (a trusted market maker).
2. Attacker (address `B`, not on allowlist) calls `addLiquidity(owner=A, salt=X, deltas=..., ...)` directly on the pool, or routes through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, A, X, ...)`.
3. The extension evaluates `allowedDepositor[pool][A]` → `true`. The guard passes.
4. Attacker `B` successfully adds liquidity to the restricted pool, paying for it themselves but depositing into `A`'s position key `(A, X)`.

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism to restrict liquidity provision to trusted or compliant actors (e.g., KYC'd market makers, whitelisted protocols). The bypass is total: any actor who knows any allowlisted address can circumvent the guard. Consequences include:

- Unauthorized actors adding liquidity to pools intended to be private/restricted, breaking the pool admin's access control invariant (admin-boundary break via unprivileged path).
- Forced liquidity injection into an allowlisted owner's position key without that owner's consent, potentially creating positions the owner did not authorize.
- In pools where the allowlist enforces regulatory compliance, the bypass exposes the protocol to compliance violations.

### Likelihood Explanation

High. The bypass requires no special privileges, no flash loans, and no complex setup. Any actor who can observe an allowlisted address (e.g., from on-chain events emitted by `setAllowedToDeposit`) can execute it in a single transaction via the public `addLiquidity` entry point or the `MetricOmmPoolLiquidityAdder`. [6](#0-5) 

### Recommendation

Check `sender` (the actual depositor/payer) instead of `owner` in `beforeAddLiquidity`:

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

If the intent is to gate both the payer and the recipient, both `sender` and `owner` should be checked.

### Proof of Concept

```solidity
// Pool configured with DepositAllowlistExtension; only `alice` is allowlisted.
// `attacker` is NOT on the allowlist.

// Step 1: admin allowlists alice only
extension.setAllowedToDeposit(address(pool), alice, true);

// Step 2: attacker calls addLiquidity with owner=alice
// The extension checks allowedDepositor[pool][alice] == true → passes
// Attacker pays for the liquidity; alice receives the position shares
vm.prank(attacker);
pool.addLiquidity(
    alice,          // owner (allowlisted) — guard checks this
    someS alt,
    deltas,
    callbackData,   // attacker's callback pays tokens
    ""
);
// Result: attacker successfully deposited into a restricted pool.
// The allowlist guard was completely bypassed.
``` [3](#0-2) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L87-95)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable returns (uint256 amount0Added, uint256 amount1Added);
```

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L211-219)
```text
  function test_exactShares_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 1, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 1, int8(4));
    assertGt(bobShares, 0);
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
