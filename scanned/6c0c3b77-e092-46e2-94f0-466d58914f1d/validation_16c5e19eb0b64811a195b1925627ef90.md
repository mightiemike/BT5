### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the user-supplied `owner` argument (the LP position recipient) rather than the `sender` argument (the actual `msg.sender` of `addLiquidity`). Because `owner` is freely chosen by the caller, any unprivileged address can bypass the allowlist by supplying an allowlisted address as `owner`, while the real depositor — the address that pays tokens through the callback — is never checked.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

- `sender` = `msg.sender` — the actual caller who will pay tokens via `metricOmmModifyLiquidityCallback`.
- `owner` — a caller-supplied address that receives the LP position shares.

`DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (first argument) and gates on `owner` instead:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is a free parameter supplied by the caller. Any address — including one that is not on the allowlist — can pass the guard by setting `owner` to any address that the pool admin has previously allowlisted.

The sister extension `SwapAllowlistExtension` correctly checks `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The naming in the admin setter (`depositor`) and the view helper (`isAllowedToDeposit(pool_, depositor)`) also confirm the intent is to gate the actual depositing address, not the LP position recipient.

---

### Impact Explanation

**Impact: Medium**

The deposit allowlist is completely ineffective. Any unprivileged address can deposit into a pool that the admin intended to restrict by:

1. Calling `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` directly, or
2. Using the official `MetricOmmPoolLiquidityAdder` router (which is itself not on any allowlist) with `owner` set to an allowlisted address.

In the router case the allowlisted user's tokens are pulled from them via approval, the LP position is credited to them, and the router — an address the pool admin never approved — is the actual `sender` that the guard should have blocked. The pool admin has no mechanism to enforce which intermediaries or contracts may deposit on behalf of allowlisted users.

The allowlist invariant — "only approved depositors may add liquidity" — is broken for every pool that deploys this extension.

---

### Likelihood Explanation

**Likelihood: High**

The bypass requires no special privilege, no flash loan, and no oracle manipulation. Any caller can craft a standard `addLiquidity` call with a chosen `owner` value. The `MetricOmmPoolLiquidityAdder` router already demonstrates the pattern (depositing on behalf of another `owner`) in its own test suite (`test_exactShares_canAddOnBehalfOfAnotherOwner`), confirming the path is reachable in normal usage.

---

### Recommendation

Replace the discarded first argument with a named `sender` parameter and gate on it, mirroring `SwapAllowlistExtension`:

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

If the intent is to allow any caller to deposit *into* an allowlisted owner's position (e.g., a trusted router depositing on behalf of a user), a separate two-level check (`allowedDepositor[pool][sender] || allowedDepositor[pool][owner]`) should be made explicit and documented.

---

### Proof of Concept

```solidity
// Assume pool has DepositAllowlistExtension configured.
// Only `alice` is on the allowlist: allowedDepositor[pool][alice] = true.
// `attacker` is NOT on the allowlist.

// Attacker calls addLiquidity with owner = alice.
// The extension checks allowedDepositor[pool][alice] == true → passes.
// Attacker pays tokens via callback; alice receives the LP shares.
// The allowlist guard is bypassed: attacker deposited into a restricted pool.

pool.addLiquidity(
    alice,          // owner — allowlisted, check passes
    salt,
    deltas,
    callbackData,   // attacker implements metricOmmModifyLiquidityCallback and pays
    extensionData
);
// No revert. Attacker has deposited into a pool the admin restricted.
```

The same path is exercised by the official `MetricOmmPoolLiquidityAdder` whenever `msg.sender` (the router) is not on the allowlist but `owner` is, which is the normal usage pattern documented in the periphery. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
