Audit Report

## Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller/payer) and checks only `owner` (the position beneficiary, a caller-supplied parameter). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender != owner`, any address can bypass the allowlist by supplying any allowlisted address as `owner`. The deposit allowlist — the pool admin's primary mechanism for curating who may provide liquidity — is completely defeated.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` (the actual caller who pays tokens) as `sender` and the caller-supplied `owner` (position beneficiary) as separate arguments to the extension hook: [1](#0-0) 

The pool's own NatSpec explicitly documents this split: *"msg.sender pays but need not equal owner (operator pattern)"*: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both `sender` and `owner` to the extension: [3](#0-2) 

However, `DepositAllowlistExtension.beforeAddLiquidity` silently drops the first argument (`sender`) and checks only `owner`: [4](#0-3) 

The check `allowedDepositor[msg.sender][owner]` keys on `owner` (the beneficiary), not on `sender` (the actual caller/payer). An unprivileged caller Eve can pass any allowlisted address as `owner` and the check passes, because `allowedDepositor[pool][alice]` is `true`. The `SwapAllowlistExtension` correctly checks `sender` (the actual caller), not `recipient`: [5](#0-4) 

The existing unit tests for `DepositAllowlistExtension` always pass `address(0)` as the `sender` argument and `depositor` as `owner`, confirming the tests only exercise the `owner` path and never test the operator-pattern bypass: [6](#0-5) 

The `MetricOmmPoolLiquidityAdder` periphery path makes the bypass even more accessible: when Eve calls `addLiquidityExactShares(pool, owner=alice, ...)`, the pool receives `sender=liquidityAdder` and `owner=alice`; the extension checks `allowedDepositor[pool][alice]` which is `true`, so Eve's tokens are deposited and Alice receives the position: [7](#0-6) 

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for curating who may provide liquidity (e.g., for compliance or risk-management). The bug completely defeats this control: any unprivileged address can deposit into a curated pool by supplying any allowlisted address as `owner`. Unauthorized LPs can dilute existing positions, alter pool composition, and circumvent any compliance or risk-management policy the admin intended to enforce. This is a broken admin-boundary / broken core pool functionality finding with direct fund-impacting consequences (unauthorized LP shares minted, pool composition altered against admin intent).

## Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no price manipulation. Any EOA or contract that knows any single allowlisted address (trivially discoverable from `AllowedToDepositSet` events) can execute the bypass in a single transaction. The `MetricOmmPoolLiquidityAdder` periphery path makes it even more accessible to EOAs. The attack is repeatable indefinitely.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller/payer) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// BEFORE (wrong actor):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// AFTER (correct actor):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

## Proof of Concept

**Setup:** Pool configured with `DepositAllowlistExtension`. Alice (`0xAlice`) is allowlisted via `setAllowedToDeposit(pool, alice, true)`. Eve (`0xEve`) is not allowlisted.

**Attack (direct pool call):**
```solidity
// Eve calls pool directly, supplying Alice as owner
pool.addLiquidity(
    /* owner = */ alice,   // allowedDepositor[pool][alice] == true → check passes
    /* salt  = */ 0,
    deltas,
    callbackData,          // Eve's callback pays Eve's tokens
    extensionData
);
// Result: Eve's tokens deposited, position credited to Alice.
```

**Attack (via LiquidityAdder):**
```solidity
// Eve approves the adder, then calls:
liquidityAdder.addLiquidityExactShares(
    pool,
    /* owner = */ alice,   // allowedDepositor[pool][alice] == true → check passes
    salt, deltas, maxAmt0, maxAmt1, extensionData
);
// pool.addLiquidity called with sender=liquidityAdder, owner=alice
// Extension checks allowedDepositor[pool][alice] → true → passes
// Eve's tokens are pulled, Alice gets the position
```

The `test_exactShares_canAddOnBehalfOfAnotherOwner` test already demonstrates the operator pattern works end-to-end; it simply does not test it against an active `DepositAllowlistExtension`: [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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

**File:** metric-periphery/test/extensions/DepositAllowlistSubExtension.t.sol (L27-41)
```text
  function test_revertsWhenDepositorNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }

  function test_passesWhenDepositorAllowed() public {
    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), depositor, true);

    vm.prank(address(pool));
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
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

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L211-220)
```text
  function test_exactShares_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    address bob = makeAddr("bob");

    vm.prank(alice);
    helper.addLiquidityExactShares(address(pool), bob, 1, d, type(uint256).max, type(uint256).max, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 1, int8(4));
    assertGt(bobShares, 0);
  }
```
