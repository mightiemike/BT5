Based on my investigation of the codebase, I found a concrete analog. Let me present the finding.

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` gates LP position `owner` instead of actual depositor `sender`, allowing non-allowlisted callers to bypass deposit restrictions — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension` is intended to restrict which addresses can deposit liquidity into a pool. Its `beforeAddLiquidity` hook checks the `owner` parameter (the LP position recipient) rather than the `sender` parameter (the actual caller who provides tokens via callback). Any non-allowlisted address can bypass the restriction by calling `addLiquidity` with `owner` set to any allowlisted address.

---

### Finding Description

In `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter — `sender` per the `IMetricOmmExtensions` interface — is unnamed and silently discarded. The allowlist check is performed against `owner`: [1](#0-0) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The `IMetricOmmExtensions` interface defines the hook signature as `beforeAddLiquidity(address sender, address owner, ...)`: [2](#0-1) 

In the pool's `addLiquidity` execution path, `sender` is `msg.sender` — the address that actually provides tokens via the pool's transfer callback — while `owner` is a caller-supplied parameter designating who receives the minted LP shares. These two addresses are independent and can be set to different values by any caller.

The admin-facing setter further confirms the intended semantics: [3](#0-2) 

The parameter is named `depositor`, which unambiguously refers to the party providing tokens (i.e., `sender`), not the LP position recipient (`owner`). The mapping `allowedDepositor[pool][depositor]` is keyed on the depositor, yet the hook reads `allowedDepositor[msg.sender][owner]` — a mismatch between intent and implementation.

The factory correctly calls `initialize` on extensions before registering the pool in `poolToIdx`, but the `DepositAllowlistExtension` does not override `initialize` and relies on the `onlyFactory`-guarded base — so the initialization path is not the root cause here. The root cause is the wrong actor being checked in the live hook. [4](#0-3) 

---

### Impact Explanation

Any non-allowlisted address can call `pool.addLiquidity(owner = allowlistedUser, ...)`. The extension receives `sender = attacker, owner = allowlistedUser`, evaluates `allowedDepositor[pool][allowlistedUser] == true`, and returns success. The attacker's tokens flow into the pool via callback; the LP shares are minted to `allowlistedUser`.

Consequences:
- The pool admin's deposit restriction is fully bypassed by any unprivileged caller.
- Unauthorized parties can inject arbitrary liquidity into a restricted pool, altering its bin distribution and effective depth at oracle-derived bid/ask prices.
- In pools where liquidity geometry is tightly controlled (e.g., institutional or permissioned pools), this breaks the invariant that only vetted LPs influence pool state.
- The `DepositAllowlistExtension` has no `beforeRemoveLiquidity` hook, so the allowlisted recipient can immediately withdraw the injected liquidity, making the attacker's token loss recoverable through social coordination with the recipient.

---

### Likelihood Explanation

The trigger requires no privilege, no special token, and no oracle manipulation. Any EOA or contract that can call `addLiquidity` and fund the callback can execute this bypass. The only prerequisite is knowing one allowlisted address, which is discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. [3](#0-2) 

---

### Recommendation

Capture and check `sender` (the actual depositor) instead of `owner`:

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

This aligns the runtime check with the semantics of `setAllowedToDeposit(pool, depositor, allowed)` and ensures the party providing tokens is the one whose allowlist status is evaluated.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` as an extension, `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. Attacker (non-allowlisted) calls `pool.addLiquidity(owner = alice, salt, deltas, "", "")`.
4. Pool invokes `DepositAllowlistExtension.beforeAddLiquidity(sender = attacker, owner = alice, ...)`.
5. Check: `allowedDepositor[pool][alice] == true` → passes; no revert.
6. Pool executes the transfer callback against `attacker`; attacker's tokens enter the pool.
7. LP shares are minted to `alice`.
8. Attacker has deposited into a restricted pool without being on the allowlist, bypassing the admin-configured access control. [1](#0-0) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-21)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);

```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L206-212)
```text
    for (uint256 i = 0; i < params.extensions.length; i++) {
      CallExtension.callExtension(
        params.extensions[i], abi.encodeCall(IMetricOmmExtensions.initialize, (pool, params.extensionInitData[i]))
      );
    }

    poolAdmin[pool] = params.admin;
```
