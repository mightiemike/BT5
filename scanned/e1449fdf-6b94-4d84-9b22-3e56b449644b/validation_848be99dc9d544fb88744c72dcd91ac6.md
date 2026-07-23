### Title
`DepositAllowlistExtension.beforeAddLiquidity()` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool." However, its `beforeAddLiquidity()` hook silently discards the `sender` argument (the actual depositor who pays tokens via callback) and validates only `owner` (the position beneficiary). Because `pool.addLiquidity()` lets any `msg.sender` supply any `owner`, an unauthorized depositor can pass the allowlist check by naming an allowlisted address as `owner`, while they themselves pay the tokens and manipulate pool bin state.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity()` drops the first parameter entirely:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool calls the hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

So `sender = msg.sender` (the actual caller who will pay tokens) and `owner` is a freely chosen parameter. These are two distinct addresses whenever a router, helper, or any third-party contract calls `addLiquidity`.

The token payment is collected from `sender` (i.e., `msg.sender` inside `LiquidityLib`), not from `owner`:

```solidity
IMetricOmmModifyLiquidityCallback(msg.sender)
    .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
``` [3](#0-2) 

The allowlist is therefore checking the wrong identity. The entity that actually injects tokens into the pool (`sender`) is never validated; only the passive position beneficiary (`owner`) is checked.

This is structurally identical to the external M-12 bug: just as `provide()` computed `addingLiquidity` from only one token's balance and ignored the other, `beforeAddLiquidity()` validates only one of the two relevant addresses and ignores the other — the one that actually matters for access control.

The asymmetry is confirmed by comparing with `SwapAllowlistExtension`, which correctly checks `sender`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

---

### Impact Explanation

An unauthorized depositor bypasses the allowlist and injects tokens into a restricted pool, corrupting its bin state. Concretely:

- Bin balances (`token0BalanceScaled`, `token1BalanceScaled`) and `binTotals` are updated for the attacker-chosen bins.
- The position is credited to the named `owner`, but the attacker controls which bins receive liquidity and in what amounts.
- Subsequent swaps execute against the manipulated bin state, potentially giving the attacker (if also a swapper) favorable execution or degrading LP returns.
- The pool admin's access-control invariant — that only allowlisted parties can alter pool liquidity — is broken by any unprivileged caller.

This is an admin-boundary break: a pool-level role check (deposit allowlist) is bypassed by an unprivileged path.

---

### Likelihood Explanation

The exploit requires no special permissions. Any EOA or contract can:
1. Observe an allowlisted `owner` address from on-chain `LiquidityAdded` events.
2. Deploy a minimal contract implementing `metricOmmModifyLiquidityCallback` that pays tokens.
3. Call `pool.addLiquidity(allowlistedOwner, salt, deltas, callbackData, "")` directly.

The check passes unconditionally because `allowedDepositor[pool][allowlistedOwner] == true`. No admin action, no special token, no oracle condition is required.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner`, mirroring `SwapAllowlistExtension`:

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

If the intent is to allowlist position owners (not callers), the NatSpec and `setAllowedToDeposit` parameter name (`depositor`) must be corrected to reflect that, and the security model must be re-evaluated — because any caller can then add liquidity on behalf of any allowlisted owner without restriction.

---

### Proof of Concept

```
Setup:
  - Pool deployed with DepositAllowlistExtension.
  - Admin calls setAllowedToDeposit(pool, alice, true).
  - Bob is NOT allowlisted.

Attack:
  1. Bob deploys AttackerCallback implementing metricOmmModifyLiquidityCallback
     that transfers token0/token1 from Bob's balance to the pool.
  2. Bob calls:
       pool.addLiquidity(
           alice,          // owner — allowlisted, check passes
           salt,
           deltas,         // attacker-chosen bins and shares
           callbackData,   // routes callback to AttackerCallback
           ""
       )
  3. Pool calls _beforeAddLiquidity(msg.sender=AttackerCallback, owner=alice, ...).
  4. DepositAllowlistExtension checks allowedDepositor[pool][alice] == true → passes.
  5. LiquidityLib updates bin state with attacker-chosen amounts.
  6. Pool calls AttackerCallback.metricOmmModifyLiquidityCallback → Bob pays tokens.
  7. Position credited to alice; bin state permanently altered by Bob.

Result: Bob, an unauthorized depositor, has modified pool liquidity in a
        restricted pool, violating the deposit allowlist invariant.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
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
