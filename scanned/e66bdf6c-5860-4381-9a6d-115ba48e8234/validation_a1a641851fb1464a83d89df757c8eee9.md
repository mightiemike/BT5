### Title
`DepositAllowlistExtension` Checks Caller-Supplied `owner` Instead of Actual `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual `msg.sender` of the pool call) and instead gates on `owner`, which is a caller-supplied, arbitrary address. Because `MetricOmmPool.addLiquidity` accepts any `owner` value without requiring it to equal `msg.sender`, any unprivileged address can bypass the deposit allowlist by passing an allowed address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` that is completely independent of `msg.sender`:

```solidity
// MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

Both `sender` (`msg.sender`) and `owner` (caller-supplied) are forwarded to the extension. [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both but silently discards `sender` (unnamed first parameter) and gates only on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

This is directly inconsistent with `SwapAllowlistExtension.beforeSwap`, which correctly gates on `sender` (the actual caller):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

The `_validateOwner` check in `MetricOmmPoolLiquidityAdder` only rejects `address(0)` — it does not enforce that `owner == msg.sender`. [4](#0-3) 

The adder's own documentation acknowledges the separation: "The position `owner` may differ from `msg.sender`, but token pulls in callback are always sourced from `msg.sender`." [5](#0-4) 

---

### Impact Explanation

The deposit allowlist is completely bypassable. A pool admin who configures `DepositAllowlistExtension` to restrict deposits to a specific set of addresses (e.g., KYC'd LPs, institutional partners) receives no protection: any unprivileged address can deposit by passing an allowed address as `owner`. LP shares are minted to `owner` (which the attacker controls or colludes with), so the attacker gains a full LP position in a restricted pool. This is an admin-boundary break — a pool admin-configured access control is bypassed by an unprivileged path with no special permissions required.

---

### Likelihood Explanation

Exploitation requires only:
1. Knowing one address that is on the allowlist (discoverable from `AllowedToDepositSet` events or `allowedDepositor` view calls).
2. Calling `pool.addLiquidity(allowedAddress, salt, deltas, callbackData, "")` directly from any address.

No privileged role, no special token approval from the victim, and no complex setup is needed. The attacker pays their own tokens and receives LP shares under the `owner` address they control.

---

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to gate on `sender` (the actual caller), consistent with `SwapAllowlistExtension`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [2](#0-1) 

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension
  - alice is allowlisted: allowedDepositor[pool][alice] = true
  - bob is NOT allowlisted

Attack:
  1. bob deploys a callback contract that pays tokens from bob's own balance
  2. bob calls pool.addLiquidity(
         owner    = alice,   // allowed address — passes the check
         salt     = 1,
         deltas   = <valid bins>,
         callbackData = <bob's callback pays tokens>,
         extensionData = ""
     )
  3. DepositAllowlistExtension.beforeAddLiquidity receives:
         sender = bob  (ignored — unnamed parameter)
         owner  = alice (checked — passes)
     → no revert
  4. LiquidityLib.addLiquidity mints shares for (alice, salt=1)
  5. Pool calls metricOmmModifyLiquidityCallback on bob
  6. Bob's callback transfers tokens to the pool
  7. LP shares are now recorded under alice (bob's controlled address)

Result: bob deposited into a restricted pool. The allowlist is bypassed.
```

The existing test `test_exactShares_canAddOnBehalfOfAnotherOwner` in `MetricOmmPoolLiquidityAdder.t.sol` already demonstrates that `alice` can mint shares for `bob` with `alice` as payer — confirming the `owner`/`sender` separation is fully reachable. [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L13-14)
```text
/// @dev The position `owner` may differ from `msg.sender`, but token pulls in callback are always sourced from
///      `msg.sender` that initiated the add call.
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
