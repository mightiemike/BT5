### Title
`DepositAllowlistExtension` Guards LP Position `owner` Instead of Transaction `sender`, Allowing Unauthorized Callers to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and gates on `owner` (the LP-position recipient) instead. Any address not on the allowlist can bypass the guard by naming an authorized address as `owner`, depositing funds into a restricted pool while the LP shares are credited to the authorized address.

---

### Finding Description

`DepositAllowlistExtension` is described as "Gates `addLiquidity` by depositor address, per pool." The `beforeAddLiquidity` hook receives two address parameters: `sender` (the `msg.sender` of the `addLiquidity` call — the entity that actually transfers funds) and `owner` (the address that will own the resulting LP position). The extension ignores `sender` entirely and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
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
``` [1](#0-0) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller) and ignores `recipient`:

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
``` [2](#0-1) 

The two extensions are structurally symmetric — one gates swaps, the other gates deposits — but the deposit extension checks the wrong actor. The `setAllowedToDeposit` setter even names its parameter `depositor`, confirming the intent is to restrict the calling entity, not the LP-position owner. [3](#0-2) 

---

### Impact Explanation

A pool admin who deploys `DepositAllowlistExtension` to enforce a closed LP set (e.g., for regulatory compliance, KYC gating, or to prevent adversarial liquidity) receives no protection against unauthorized callers. Any address can call `addLiquidity` with `owner` set to any address already on the allowlist, and the hook will pass. The unauthorized caller's funds enter the pool; the LP shares are minted to the named `owner`. This constitutes an admin-boundary break: an unprivileged path bypasses a pool-admin-configured access control, violating the invariant that only authorized depositors can supply liquidity to the pool.

---

### Likelihood Explanation

The bypass requires no special privileges, no flash loan, and no oracle manipulation. Any external account can execute it in a single transaction by inspecting the allowlist (public mapping) and naming any authorized address as `owner`. The only cost to the attacker is the gas and the deposited tokens (which go to the named `owner`). Pools that rely on `DepositAllowlistExtension` for access control are fully exposed from the moment of deployment.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it, matching the pattern in `SwapAllowlistExtension`:

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
```

If the intended semantic is to restrict LP-position ownership (not the calling entity), the NatSpec, setter parameter name (`depositor`), and the parallel with `SwapAllowlistExtension` should all be updated to reflect that intent, and the `isAllowedToDeposit` view function should be renamed accordingly.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and adds only `Alice` to the allowlist via `setAllowedToDeposit(pool, alice, true)`.
2. `Bob` (not on the allowlist) calls `pool.addLiquidity(...)` with `owner = alice`.
3. Inside `beforeAddLiquidity`, `msg.sender = pool`, `owner = alice`. The check `allowedDepositor[pool][alice]` returns `true` → no revert.
4. `Bob`'s tokens are transferred into the pool; LP shares are minted to `alice`.
5. The pool admin's deposit restriction is fully bypassed: `Bob` successfully added liquidity to a pool he was never authorized to touch.

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
