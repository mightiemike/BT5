### Title
`DepositAllowlistExtension.beforeAddLiquidity` Guards `owner` Instead of `sender`, Allowing Any Actor to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently ignores the `sender` parameter (the actual caller who pays tokens) and instead validates `owner` (the LP position beneficiary). Because `MetricOmmPool.addLiquidity` explicitly permits `msg.sender != owner` (the "operator pattern"), any unprivileged actor can bypass the allowlist by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

- `sender` = `msg.sender` — the caller who pays tokens via the swap callback
- `owner` — the address that receives LP shares (position key) [1](#0-0) 

The pool's own NatSpec acknowledges the split: *"msg.sender pays but need not equal owner (operator pattern)."* [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both addresses but discards `sender` (unnamed first parameter) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The extension's own interface, events, and admin setters all use the term **depositor** — not owner — confirming the intended subject of the check is the actual depositing actor: [4](#0-3) 

Because the check is on `owner` rather than `sender`, the guard is trivially bypassed: an unauthorized caller specifies any allowlisted address as `owner`, the extension approves the call, and the unauthorized caller's tokens enter the pool.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., KYC/AML compliance, institutional-only pools, DAO-gated pools). With this bug the control is entirely ineffective:

- Any actor, regardless of allowlist status, can deposit tokens into a restricted pool.
- The pool admin's configured security boundary is silently bypassed on every `addLiquidity` call where `msg.sender != owner`.
- Tokens from unauthorized sources enter pool bins and affect bin balances, LP share accounting, and fee accrual — all of which are core pool state.

This is an admin-boundary break: an unprivileged path circumvents a factory-configured extension guard, matching the allowed impact gate.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no complex setup. Any EOA or contract can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` with a known allowlisted address as `owner`. The allowlisted addresses are discoverable on-chain via `allowedDepositor` mapping reads. Likelihood is **High**.

---

### Recommendation

Replace the `owner` check with a `sender` check so the guard validates the actual depositing actor:

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
``` [3](#0-2) 

---

### Proof of Concept

**Setup:**
- Pool `P` is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
- Pool admin calls `setAllowedToDeposit(P, alice, true)` → `allowedDepositor[P][alice] = true`.
- Bob (`0xB0b...`) is **not** on the allowlist.

**Attack:**
1. Bob deploys a contract implementing `IMetricOmmModifyLiquidityCallback` that pays tokens from Bob's balance.
2. Bob's contract calls:
   ```solidity
   pool.addLiquidity(
       alice,          // owner — allowlisted address
       0,              // salt
       deltas,         // desired bin/share allocation
       callbackData,   // Bob's contract pays tokens here
       ""
   );
   ```
3. Pool calls `DepositAllowlistExtension.beforeAddLiquidity(bob_contract, alice, ...)`.
4. Extension evaluates `allowedDepositor[P][alice]` → `true` → **no revert**.
5. Pool credits LP shares to `alice`; Bob's tokens are transferred into the pool.

**Result:** Bob successfully deposited into an allowlist-restricted pool. The allowlist check on `sender` (Bob) was never performed.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-148)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
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

**File:** metric-periphery/contracts/interfaces/extensions/IDepositAllowlistExtension.sol (L7-18)
```text
  event AllowedToDepositSet(address indexed pool, address indexed depositor, bool allowed);
  event AllowAllDepositorsSet(address indexed pool, bool allowed);

  function allowedDepositor(address pool, address depositor) external view returns (bool);

  function allowAllDepositors(address pool) external view returns (bool);

  function setAllowedToDeposit(address pool, address depositor, bool allowed) external;

  function setAllowAllDepositors(address pool, bool allowed) external;

  function isAllowedToDeposit(address pool, address depositor) external view returns (bool);
```
