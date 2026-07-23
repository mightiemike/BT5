### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Supplied `owner` Instead of Actual Depositor, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook receives the actual caller as its first (unnamed, ignored) parameter and the caller-supplied LP-position `owner` as its second parameter. The guard checks only `owner`, which any caller can freely set to any allowed address, making the allowlist completely bypassable by any unprivileged address.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` argument (the LP-position recipient) and fires the hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

So the hook's first `address` parameter is the **actual depositor** (`msg.sender` of `addLiquidity`) and the second is the **LP-position owner** (caller-supplied). `DepositAllowlistExtension.beforeAddLiquidity` silently discards the first parameter and checks only `owner`:

```solidity
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
``` [2](#0-1) 

Because `owner` is a free argument chosen by the caller, any address can pass the guard by supplying any address that is already on the allowlist (e.g., the pool admin, or any other known-allowed LP). The actual depositor — the address that implements the callback and transfers tokens — is never verified.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks the first parameter (`sender`, the actual swapper) and ignores the second (`recipient`):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

The asymmetry confirms the deposit check is bound to the wrong identity.

---

### Impact Explanation

The `DepositAllowlistExtension` allowlist is rendered completely inoperative. Any unprivileged address can add liquidity to a pool that the admin intended to restrict, by setting `owner` to any address already on the allowlist. Consequences include:

- **Admin-boundary break via unprivileged path**: the pool admin's access-control invariant ("only approved depositors may add liquidity") is silently violated on every call from an unauthorized depositor.
- **Pool state manipulation**: unauthorized liquidity additions alter bin balances and `binTotals`, shifting the pool's internal price position and potentially triggering or suppressing other extensions (e.g., `OracleValueStopLossExtension` watermarks, `PriceVelocityGuardExtension` state).
- **LP share misattribution**: LP shares are credited to the `owner` address (an allowed party who did not provide tokens), while the actual token provider holds no position and cannot recover funds — a structural locked-fund outcome analogous to the seeded bug class.

---

### Likelihood Explanation

Exploitation requires only a single `addLiquidity` call with `owner` set to any known-allowed address (the pool admin is always a candidate). No special permissions, flash loans, or oracle manipulation are needed. The bypass is deterministic and repeatable.

---

### Recommendation

Check the actual depositor (first parameter) instead of `owner`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external
      view
      override
      returns (bytes4)
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

1. Pool is deployed with `DepositAllowlistExtension` configured; `allowedDepositor[pool][alice] = true`; `allowedDepositor[pool][attacker] = false`.
2. `attacker` calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool fires `_beforeAddLiquidity(attacker, alice, ...)`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` calls back to `attacker` (the actual `msg.sender`) to pull tokens; `attacker` pays.
6. LP shares are minted to `alice`; `attacker` has deposited into a restricted pool without being on the allowlist.

The allowlist check is fully bypassed. `attacker`'s tokens are now locked in the pool under `alice`'s position — `attacker` holds no shares and cannot withdraw, while `alice` received shares without providing capital.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
