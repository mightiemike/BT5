### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` but Ignores `sender`, Allowing Any Unprivileged Operator to Bypass the Deposit Allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is the pool-admin-configured guard that gates `addLiquidity`. Its `beforeAddLiquidity` override silently drops the `onlyPool` modifier present in the base class and, critically, only inspects the `owner` argument while ignoring the `sender` argument. Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender` (the payer) need not equal `owner` (the LP-position recipient), any unprivileged address can call `pool.addLiquidity(allowlistedOwner, …)` and pass the guard by borrowing an allowlisted owner's identity, depositing liquidity the pool admin intended to block.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the operator/payer; `owner` is the LP-position beneficiary. The interface and NatSpec explicitly document this split:

> "msg.sender pays but need not equal owner (operator pattern)."

`DepositAllowlistExtension.beforeAddLiquidity` receives both but only acts on `owner`:

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override          // ← drops onlyPool from BaseMetricExtension
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` here is the pool (correct for the pool-keyed mapping lookup), but the first function parameter — the actual depositing operator — is unnamed and never read. The guard therefore passes whenever `owner` is on the allowlist, regardless of who `sender` is.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swap initiator), not `recipient`, so no analogous bypass exists on the swap path.

---

### Impact Explanation

The pool admin deploys `DepositAllowlistExtension` to enforce a closed LP set — e.g., KYC-gated depositors, a curated set of market-makers, or a regulatory whitelist. Any address outside that set is supposed to be blocked from providing liquidity.

Because the guard only checks `owner`, an unprivileged attacker can:

1. Call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` directly (bypassing the periphery router's `_validateOwner` check, which only prevents `address(0)`).
2. The extension sees `allowedDepositor[pool][allowlistedAddress] == true` and returns the success selector.
3. The pool proceeds; the attacker's `metricOmmModifyLiquidityCallback` pays the tokens; LP shares are minted under `allowlistedAddress`.

Concrete consequences:
- The admin-configured allowlist is fully neutralised — any actor can inject liquidity.
- LP shares are minted to an address that did not initiate or consent to the deposit, which can be used to grief or manipulate that address's position accounting.
- If the pool's LP composition is security-sensitive (e.g., the allowlist prevents a known adversarial LP from influencing bin prices or stop-loss watermarks), the bypass directly undermines those downstream guards.

This is an admin-boundary break: an unprivileged path circumvents a pool-admin-configured access control with fund-relevant consequences (LP share issuance, pool token inflows).

---

### Likelihood Explanation

- The operator pattern (`sender ≠ owner`) is a first-class, documented feature of `MetricOmmPool.addLiquidity`.
- No on-chain check prevents a direct call to `pool.addLiquidity` with an arbitrary `owner`; the periphery router is optional.
- The attacker only needs to know one allowlisted address (observable on-chain via `AllowedToDepositSet` events or `allowedDepositor` public mapping reads).
- No special privilege, flash loan, or oracle manipulation is required.

Likelihood: **High** (trivially reachable by any EOA or contract).

---

### Recommendation

Check both `sender` and `owner` in `beforeAddLiquidity`. The guard should require that the actual depositing operator is also allowlisted:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    onlyPool   // restore the base-class guard
    returns (bytes4)
{
    address pool_ = msg.sender;
    bool senderOk = allowAllDepositors[pool_] || allowedDepositor[pool_][sender];
    bool ownerOk  = allowAllDepositors[pool_] || allowedDepositor[pool_][owner];
    if (!senderOk || !ownerOk) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Alternatively, if the intent is only to gate the LP-position owner (not the payer), the NatSpec and admin-facing documentation must be updated to make that explicit, and the `setAllowedToDeposit` / `setAllowAllDepositors` API should be renamed accordingly so pool admins are not misled into believing they are restricting the depositing actor.

---

### Proof of Concept

```
Setup:
  - Factory deploys pool with DepositAllowlistExtension on beforeAddLiquidity.
  - Pool admin calls extension.setAllowedToDeposit(pool, alice, true).
    → allowedDepositor[pool][alice] = true
  - bob is NOT on the allowlist.

Attack:
  1. bob calls pool.addLiquidity(alice, salt, deltas, callbackData, "")
       ↳ pool calls extension.beforeAddLiquidity(bob, alice, salt, deltas, "")
       ↳ extension checks allowedDepositor[pool][alice] == true  ✓
       ↳ extension returns selector — guard passes
  2. pool calls bob.metricOmmModifyLiquidityCallback(amount0, amount1, callbackData)
       ↳ bob pays token0/token1
  3. pool mints LP shares under (alice, salt)

Result:
  - bob (unprivileged) has successfully deposited into an allowlist-gated pool.
  - alice holds LP shares she did not initiate.
  - The deposit allowlist is completely bypassed.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L45-52)
```text
  function beforeAddLiquidity(address, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```
