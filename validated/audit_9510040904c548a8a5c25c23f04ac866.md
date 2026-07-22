### Title
`DepositAllowlistExtension` validates `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces its allowlist against the `owner` parameter (the position recipient) rather than the `sender` parameter (the actual caller who provides tokens). Because `MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` with no restriction on who the caller is, any unprivileged address can bypass the allowlist entirely by setting `owner` to any address that is already on the allowlist.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument and the caller-supplied `owner` as the second argument to `_beforeAddLiquidity`:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives these as its first two `address` parameters. The first parameter (the actual caller / token provider) is unnamed and silently discarded. The guard is evaluated only against `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

Because `addLiquidity` imposes no constraint that `msg.sender == owner`, any address can call `addLiquidity(allowlistedAddress, salt, deltas, ...)`. The extension sees `owner = allowlistedAddress`, the check passes, and the unauthorized caller's tokens flow into the pool via the swap callback, with the resulting position credited to the allowlisted address.

The asymmetry is visible by contrast with `SwapAllowlistExtension`, which correctly checks the first parameter (`sender`):

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

`SwapAllowlistExtension` names and checks `sender`; `DepositAllowlistExtension` discards `sender` and checks `owner` instead.

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole on-chain mechanism for restricting liquidity provision to permissioned pools (KYC pools, private LP pools, etc.). With this bug the guard is completely ineffective:

- Any address can deposit tokens into a pool that is supposed to be access-controlled.
- The deposited tokens are locked into a position owned by the allowlisted address; the allowlisted address can then call `removeLiquidity` and withdraw them, effectively laundering the deposit through a KYC'd identity.
- Pool invariants that depend on knowing who the LPs are (regulatory compliance, fee-tier eligibility, etc.) are silently violated.
- The pool admin has no on-chain recourse: the allowlist setter (`setAllowedToDeposit`) cannot fix the bypass because the root cause is in the parameter the extension reads, not in the allowlist data itself.

This constitutes broken core pool functionality (the access-control extension does not function as specified) and an admin-boundary break (an unprivileged path bypasses the factory/pool-admin-configured allowlist).

---

### Likelihood Explanation

The attack requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can exploit it in a single transaction by calling `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)`. The allowlisted address is public on-chain via `allowedDepositor`. Likelihood is **High**.

---

### Recommendation

Change `beforeAddLiquidity` to check the first parameter (`sender`) — the actual caller who provides tokens — instead of `owner`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
``` [2](#0-1) 

This mirrors the correct pattern already used in `SwapAllowlistExtension`.

---

### Proof of Concept

```
Setup:
  - Pool P is deployed with DepositAllowlistExtension E.
  - Pool admin calls E.setAllowedToDeposit(P, alice, true).
  - bob is NOT on the allowlist.

Attack:
  1. bob calls P.addLiquidity(
         owner    = alice,   // allowlisted address
         salt     = 0,
         deltas   = <desired bins/shares>,
         callbackData = ..., // bob pays tokens in the callback
         extensionData = ""
     )
  2. Pool calls E.beforeAddLiquidity(bob, alice, ...).
     Extension checks allowedDepositor[P][alice] == true → passes.
  3. Pool calls bob's metricOmmSwapCallback; bob transfers tokens to the pool.
  4. Position is minted for alice.
  5. alice calls P.removeLiquidity(alice, 0, deltas, "") and withdraws bob's tokens.

Result: bob deposited into a restricted pool; the allowlist guard was never triggered.
```

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
