The code confirms all three cited facts. Let me verify the key details:

1. `DepositAllowlistExtension.beforeAddLiquidity` at L32 has the first parameter (`sender`) unnamed and ignored — the check at L38 uses `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool and `owner` is the position beneficiary. [1](#0-0) 

2. `MetricOmmPool.addLiquidity` has no `msg.sender == owner` guard — it passes `msg.sender` as `sender` and the caller-supplied `owner` directly to `_beforeAddLiquidity`. [2](#0-1) 

3. `ExtensionCalling._beforeAddLiquidity` correctly forwards both `sender` and `owner` to the extension, but the extension discards `sender`. [3](#0-2) 

4. `removeLiquidity` does enforce `msg.sender == owner` (L206), but `addLiquidity` does not. [4](#0-3) 

The vulnerability is real and traceable through the exact code path described.

---

Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and authorizes the call based solely on `owner` (the position beneficiary). Because `MetricOmmPool.addLiquidity` accepts a freely caller-supplied `owner` with no `msg.sender == owner` guard, any unprivileged address can pass the allowlist check by nominating an already-allowlisted address as `owner`. The pool admin's sole access-control mechanism for restricting deposits is entirely defeated.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is declared with `sender` as an unnamed, ignored parameter:

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
```

`msg.sender` inside the extension is the pool, so the lookup is `allowedDepositor[pool][owner]` — it checks whether the *beneficiary* is allowlisted, not the *actual caller*.

`MetricOmmPool.addLiquidity` has no `msg.sender == owner` guard (contrast with `removeLiquidity` at L206 which does enforce `msg.sender == owner`):

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` correctly encodes both `sender` (the real caller) and `owner` (the beneficiary) and passes them to the extension — but the extension ignores `sender`.

**Attack path:**
1. Pool admin deploys pool with `DepositAllowlistExtension` and sets `allowedDepositor[pool][alice] = true`.
2. Attacker (not on the allowlist) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)` directly.
3. Pool calls `_beforeAddLiquidity(msg.sender=attacker, owner=alice, ...)`.
4. Extension receives `(sender=attacker [ignored], owner=alice)` and checks `allowedDepositor[pool][alice]` → `true`. Check passes.
5. Pool invokes `metricOmmModifyLiquidityCallback` on the attacker to pull tokens. Attacker pays; LP shares are credited to `alice`.
6. The attacker has deposited into the pool despite not being allowlisted.

## Impact Explanation

`DepositAllowlistExtension` is the sole mechanism for restricting pool participation to authorized depositors (KYC/compliance gate). Any unprivileged address can bypass it by nominating any allowlisted address as `owner`. The pool admin's access control is entirely defeated: the pool accepts deposits from actors it was configured to exclude. This constitutes an admin-boundary break via an unprivileged path — a broken core pool functionality matching the contest's allowed impact gate.

## Likelihood Explanation

The bypass requires only a direct call to `pool.addLiquidity` with a known allowlisted address as `owner` and a valid `metricOmmModifyLiquidityCallback` implementation. No privileged access, no special state, no flash loan. Any on-chain observer can identify allowlisted addresses from `AllowedToDepositSet` events and execute the bypass immediately. The attack is repeatable and unconditional.

## Recommendation

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

## Proof of Concept

```solidity
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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
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
