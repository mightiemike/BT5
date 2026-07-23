### Title
`DepositAllowlistExtension.beforeAddLiquidity` Ignores `sender` and Gates Only on `owner`, Allowing Any Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` parameter (the actual caller who pays the tokens) and only checks `owner` (the LP-position beneficiary). Any non-allowlisted address can bypass the deposit guard by calling `pool.addLiquidity(owner = allowedUser, …)` with an allowlisted address as `owner`. The hook passes, the deposit executes, and the curated-pool protection fails open — a direct analog to the Liquity "redeem with no redeemable troves" pattern where a guard that should revert instead silently succeeds.

---

### Finding Description

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its hook signature explicitly drops the first `address` argument (the caller) and checks only the second (`owner`):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The pool calls the hook with `msg.sender` as `sender` and the user-supplied `owner` as the position beneficiary:

```solidity
// metric-core/contracts/MetricOmmPool.sol  L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both:

```solidity
// metric-core/contracts/ExtensionCalling.sol  L95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

Because the extension discards `sender` and checks only `owner`, the following attack path is open to any address:

1. Attacker (`Bob`, not allowlisted) calls `pool.addLiquidity(owner = Alice, …)` where `Alice` is allowlisted.
2. The hook evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
3. The pool mints LP shares credited to `Alice`; `Bob` pays the tokens via the callback.
4. `Bob` has successfully interacted with the curated pool and altered its state despite being explicitly excluded.

The `sender` parameter is unnamed and structurally ignored — this is not a subtle off-by-one; the guard simply never reads the actual caller.

---

### Impact Explanation

A curated pool deploying `DepositAllowlistExtension` to enforce KYC, compliance, or whitelist-only LP membership receives no protection against the payer. Any address — including sanctioned or explicitly excluded addresses — can deposit into the pool by nominating any allowlisted address as `owner`. The allowlisted address receives unsolicited LP shares; the non-allowlisted address has successfully altered pool state and bypassed the access control the pool admin believed was enforced. This breaks the admin-boundary invariant: an unprivileged path (direct `addLiquidity` with a crafted `owner`) defeats a configured guard without any privileged action.

---

### Likelihood Explanation

Exploitation requires no special privileges, no flash loan, and no oracle manipulation. Any externally-owned account can call `pool.addLiquidity` directly with `owner` set to any address that appears in the allowlist (which is public on-chain via `allowedDepositor`). The attack is unconditional and repeatable.

---

### Recommendation

The hook must check the **caller** (`sender`), not the position beneficiary (`owner`). Replace the unnamed first parameter with a named one and gate on it:

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

If the design intent is to gate the LP-position owner (not the payer), the NatSpec and variable names must be corrected and a separate payer check added, because the current code leaves the payer entirely ungated.

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  Alice  → allowedDepositor[pool][Alice] = true
  Bob    → allowedDepositor[pool][Bob]   = false  (not allowlisted)

Attack:
  vm.prank(Bob);
  pool.addLiquidity(
      owner        = Alice,   // allowlisted — hook checks this and passes
      salt         = 0,
      deltas       = { binIdxs: [0], shares: [1000] },
      callbackData = "",
      extensionData = ""
  );
  // Bob's callback pays token0/token1; Alice receives 1000 LP shares.
  // Hook never evaluated Bob's address; guard silently passed.

Result:
  Bob has deposited into a curated pool he is explicitly excluded from.
  Pool admin's allowlist protection is fully bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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
