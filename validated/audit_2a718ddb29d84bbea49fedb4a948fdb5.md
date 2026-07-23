Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and gates only on `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` explicitly permits `msg.sender ≠ owner` via the operator pattern, any unprivileged address can call `addLiquidity` with `owner` set to an allowlisted address, causing the allowlist check to pass on the allowlisted owner while the real (non-allowlisted) caller pays tokens and the pool mints LP shares — fully bypassing the admin-configured deposit restriction.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as separate arguments to the extension dispatcher:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly forwards both to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first parameter but explicitly discards it (unnamed), checking only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

The pool's own NatSpec documents the operator pattern that makes this exploitable — `msg.sender` pays but need not equal `owner`: [4](#0-3) 

There is no guard anywhere in `addLiquidity` that independently checks `msg.sender` against the allowlist. The extension is the sole enforcement mechanism, and it checks the wrong identity. The `removeLiquidity` path enforces `msg.sender == owner`, but `addLiquidity` has no such constraint: [5](#0-4) 

The exploit path:
1. Alice is allowlisted: `allowedDepositor[pool][alice] = true`. Bob is not allowlisted.
2. Bob calls `pool.addLiquidity(owner = alice, salt = 0, deltas = ..., callbackData = ..., extensionData = "")`.
3. Pool calls `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)` → extension checks `allowedDepositor[pool][alice]` → `true` → passes.
4. `LiquidityLib.addLiquidity` mints shares to position key `(alice, 0)`.
5. Pool calls `IMetricOmmModifyLiquidityCallback(Bob).metricOmmModifyLiquidityCallback(...)` → Bob pays token0/token1.

Result: Bob (not allowlisted) injects tokens into the curated pool; Alice receives LP shares she did not request.

The wrong value is `allowedDepositor[msg.sender][owner]` — `owner` (Alice) is checked instead of the first parameter `sender` (Bob, the actual depositor). [6](#0-5) 

## Impact Explanation

The deposit allowlist is the pool admin's sole mechanism to curate who participates in the pool. Bypassing it allows an unprivileged caller to:
1. Inject tokens into a curated pool without being allowlisted — breaking the admin-configured boundary.
2. Force an allowlisted address to receive LP shares it did not request — griefing that address with unwanted pool exposure and potential impermanent loss.
3. Manipulate pool cursor and bin balances in a pool restricted to vetted participants, potentially harming existing allowlisted LPs.

This is a broken core pool functionality / admin-boundary break with direct LP asset impact, meeting the contest's Critical/High threshold.

## Likelihood Explanation

The exploit requires no special privileges, no flash loans, and no complex setup. Any EOA or contract can call `pool.addLiquidity` directly with `owner` set to any known allowlisted address. The allowlisted address does not need to cooperate. The attacker only needs to fund the callback payment. The operator pattern is explicitly documented and supported by the pool, making this trivially reachable.

## Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual caller) rather than `owner` (the position recipient):

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

## Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension as EXTENSION_1
  - Alice (0xAlice) is allowlisted: allowedDepositor[pool][0xAlice] = true
  - Bob (0xBob) is NOT allowlisted

Attack:
  1. Bob calls pool.addLiquidity(
         owner    = 0xAlice,
         salt     = 0,
         deltas   = { binIdxs: [activebin], shares: [1000] },
         callbackData = abi.encode(...),
         extensionData = ""
     )

  2. Pool calls _beforeAddLiquidity(sender=0xBob, owner=0xAlice, ...)
     → DepositAllowlistExtension checks allowedDepositor[pool][0xAlice] → true → passes

  3. LiquidityLib.addLiquidity mints shares to position key (0xAlice, 0)

  4. Pool calls IMetricOmmModifyLiquidityCallback(0xBob).metricOmmModifyLiquidityCallback(...)
     → Bob pays token0/token1 amounts

Result:
  - Bob (not allowlisted) has paid tokens into the curated pool ✓
  - Alice receives LP shares she did not request ✓
  - Allowlist is bypassed with zero special privileges ✓

Foundry test skeleton:
  function test_allowlist_bypass() public {
      // alice is allowlisted, bob is not
      vm.prank(poolAdmin);
      depositAllowlist.setAllowedToDeposit(address(pool), alice, true);

      // bob calls addLiquidity with owner = alice
      vm.prank(bob);
      pool.addLiquidity(alice, 0, deltas, callbackData, "");

      // bob paid, alice got shares — allowlist bypassed
      assertGt(stateView.positionBinShares(address(pool), alice, 0, activeBin), 0);
  }
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-147)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
```
