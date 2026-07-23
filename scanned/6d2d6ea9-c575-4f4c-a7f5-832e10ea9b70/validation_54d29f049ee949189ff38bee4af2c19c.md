The vulnerability is real and traceable through the exact code path. Here is the full analysis:

---

### Title
DepositAllowlistExtension Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and authorizes the call based solely on `owner` (the position beneficiary). Because `MetricOmmPool.addLiquidity` accepts a freely caller-supplied `owner` with no `msg.sender == owner` guard, any unprivileged address can pass the allowlist check by nominating an already-allowlisted address as `owner`.

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is declared with `sender` as an unnamed, ignored parameter:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The check is `allowedDepositor[pool][owner]`. `msg.sender` here is the pool (the extension caller), so the lookup key is `(pool, owner)`.

`MetricOmmPool.addLiquidity` has no `msg.sender == owner` guard:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L182-195
function addLiquidity(address owner, ...) external nonReentrant(...) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    ...
}
``` [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` passes both `sender` (the real caller) and `owner` (the beneficiary) to the extension, but the extension ignores `sender`: [3](#0-2) 

**Attack path:**

1. Pool admin deploys pool with `DepositAllowlistExtension` and sets `allowedDepositor[pool][alice] = true`.
2. Attacker (not on the allowlist) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)` directly.
3. Pool calls `_beforeAddLiquidity(msg.sender=attacker, owner=alice, ...)`.
4. Extension receives `(sender=attacker [ignored], owner=alice)` and checks `allowedDepositor[pool][alice]` → `true`. Check passes.
5. Pool calls `metricOmmModifyLiquidityCallback` on the attacker to pull tokens. Attacker pays; LP shares are credited to `alice`.
6. The attacker has deposited into the pool despite not being allowlisted.

The periphery router `_validateOwner` only rejects `address(0)` — it does not enforce `owner == msg.sender`: [4](#0-3) 

The router even has a test explicitly confirming that alice can add liquidity for bob's position with no allowlist check on the caller: [5](#0-4) 

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism for restricting pool participation to authorized depositors (KYC/compliance gate). Any unprivileged address can bypass it by nominating any allowlisted address as `owner`. The attacker pays the tokens; the allowlisted address receives unsolicited LP shares they did not authorize. The pool admin's access control is entirely defeated: the pool accepts deposits from actors it was configured to exclude. This constitutes a broken core pool functionality (admin-boundary break via an unprivileged path).

### Likelihood Explanation

The bypass requires only a direct call to `pool.addLiquidity` with a known allowlisted address as `owner` and a valid `metricOmmModifyLiquidityCallback` implementation. No privileged access, no special state, no flash loan. Any on-chain observer can identify allowlisted addresses from `AllowedToDepositSet` events and execute the bypass immediately.

### Recommendation

`beforeAddLiquidity` must check `sender` (the actual caller), not `owner` (the beneficiary). Change the check to:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the operator pattern (one address paying for another's position) must be preserved, the extension should require that both `sender` and `owner` are allowlisted, or introduce an explicit operator-approval mapping.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistBypass() public {
    // Setup: pool with DepositAllowlistExtension; alice is allowlisted, attacker is not
    depositExtension.setAllowedToDeposit(address(pool), alice, true);
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker));

    // Attacker calls pool directly with alice as owner
    vm.startPrank(attacker);
    // attacker must implement IMetricOmmModifyLiquidityCallback to pay tokens
    pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
    vm.stopPrank();

    // Assert: deposit succeeded despite attacker not being allowlisted
    uint256 aliceShares = stateView.positionBinShares(address(pool), alice, salt, binIdx);
    assertGt(aliceShares, 0); // allowlist bypassed
}
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
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
