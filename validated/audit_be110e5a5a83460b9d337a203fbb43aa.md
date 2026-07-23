### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller/payer) and gates access on `owner` (the position beneficiary) instead. Because `addLiquidity` explicitly supports an operator pattern where `msg.sender != owner`, any address can bypass the allowlist by supplying any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes both `msg.sender` (the actual caller who pays tokens) and `owner` (the position beneficiary, a caller-supplied parameter) to the extension hook: [1](#0-0) 

The pool's own NatSpec documents this split explicitly: *"msg.sender pays but need not equal owner (operator pattern)"*. [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both actors to the extension: [3](#0-2) 

However, `DepositAllowlistExtension.beforeAddLiquidity` silently drops the first argument (`sender`) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [4](#0-3) 

This is the exact structural analog of the ERC20 `allowance[_from][_to]` bug: the guard is keyed to the wrong actor. The `SwapAllowlistExtension` correctly checks `sender` (the actual caller), not `recipient`: [5](#0-4) 

---

### Impact Explanation

Any address not on the allowlist can deposit into a curated pool by calling `addLiquidity(owner = <any allowlisted address>, ...)` directly on the pool, or via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner = <any allowlisted address>, ...)`. [6](#0-5) 

The deposit allowlist — the pool admin's primary mechanism for curating who may provide liquidity — is completely defeated. Unauthorized LPs can dilute existing positions, alter pool composition, and circumvent any compliance or risk-management policy the admin intended to enforce. This is a broken admin-boundary / broken core pool functionality finding.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no price manipulation. Any EOA or contract that knows any single allowlisted address (trivially discoverable from `AllowedToDepositSet` events) can execute the bypass in a single transaction. The `MetricOmmPoolLiquidityAdder` periphery path makes it even more accessible.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller/payer) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
// BEFORE (wrong actor):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// AFTER (correct actor):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [4](#0-3) 

---

### Proof of Concept

**Setup:** Pool is configured with `DepositAllowlistExtension`. Alice (`0xAlice`) is allowlisted. Eve (`0xEve`) is not.

**Attack (direct pool call):**

```solidity
// Eve calls pool directly, supplying Alice as owner
pool.addLiquidity(
    /* owner = */ alice,   // allowlisted → check passes
    /* salt  = */ 0,
    deltas,
    callbackData,          // Eve's callback pays Eve's tokens
    extensionData
);
// Result: Eve's tokens deposited, position credited to Alice.
// Eve has interacted with a curated pool she was not authorized to use.
```

**Attack (via LiquidityAdder):**

```solidity
// Eve approves the adder, then calls:
liquidityAdder.addLiquidityExactShares(
    pool,
    /* owner = */ alice,   // allowlisted → check passes
    salt, deltas, maxAmt0, maxAmt1, extensionData
);
// pool.addLiquidity is called with sender=liquidityAdder, owner=alice
// Extension checks allowedDepositor[pool][alice] → true → passes
// Eve's tokens are pulled, Alice gets the position
```

The `test_exactShares_canAddOnBehalfOfAnotherOwner` test in the periphery suite already demonstrates that the operator pattern works end-to-end; it simply does not test it against an active `DepositAllowlistExtension`. [7](#0-6)

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
