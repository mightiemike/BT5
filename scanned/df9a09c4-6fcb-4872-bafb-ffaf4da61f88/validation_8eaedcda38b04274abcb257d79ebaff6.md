### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter and gates on `owner` instead. Because `pool.addLiquidity` accepts a freely-chosen `owner` with no requirement that `owner == msg.sender`, any caller can pass an allowlisted address as `owner` and the allowlist check passes unconditionally, even though the actual depositor (token provider) is not allowlisted.

---

### Finding Description

`pool.addLiquidity` passes both `msg.sender` (the actual caller/token-provider) and the caller-supplied `owner` (the LP-position recipient) to the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling` forwards both as `(sender, owner, ...)`: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter unnamed (discarded) and checks only `owner`: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, ...)  // sender silently dropped
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert NotAllowedToDeposit();
    }
```

`pool.addLiquidity` has no constraint that `owner == msg.sender`: [4](#0-3) 

The `_validateOwner` guard in the periphery router only rejects `address(0)` — it does not enforce `owner == msg.sender` — and the attacker can bypass the router entirely and call the pool directly: [5](#0-4) 

---

### Impact Explanation

An attacker calls `pool.addLiquidity(owner=victim)` where `victim` is allowlisted. The extension checks `allowedDepositor[pool][victim] == true` and passes. The attacker's callback provides the tokens; the LP position is minted to `victim`. The pool admin's deposit allowlist — the sole curation mechanism of `DepositAllowlistExtension` — is completely defeated by any unprivileged caller. This is a full admin-boundary break: the access control the pool admin configured is bypassed without any privileged role.

Secondary effects:
- The attacker irrecoverably loses their tokens (the position is under `victim`; `removeLiquidity` enforces `msg.sender == owner`).
- The victim receives unsolicited LP shares (redeemable, but forced on them).
- Any pool relying on `DepositAllowlistExtension` for KYC/compliance/curation has that guarantee nullified.

---

### Likelihood Explanation

The attack requires only a public `pool.addLiquidity` call with a known allowlisted address as `owner`. No privileged role, no special token, no oracle manipulation. Any address can execute it at any time. The only cost is the attacker's own tokens (which go to the victim's position).

---

### Recommendation

Check `sender` (the actual depositor/token-provider), not `owner` (the LP-position recipient):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_allowlistBypass() public {
    // Setup: victim is allowlisted, attacker is not
    extension.setAllowedToDeposit(address(pool), victim, true);
    // allowedDepositor[pool][attacker] == false

    // Attacker calls pool.addLiquidity directly with owner=victim
    vm.prank(attacker);
    // attacker implements IMetricOmmModifyLiquidityCallback to pay tokens
    pool.addLiquidity(victim, salt, deltas, callbackData, "");

    // Assert: deposit succeeded despite attacker not being allowlisted
    uint256 victimShares = pool.positionBinShares(victim, salt, bin);
    assertGt(victimShares, 0); // passes — allowlist bypassed
}
```

The `beforeAddLiquidity` hook receives `sender=attacker` (discarded) and `owner=victim` (checked), so `allowedDepositor[pool][victim] == true` passes the guard. [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-248)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
```
