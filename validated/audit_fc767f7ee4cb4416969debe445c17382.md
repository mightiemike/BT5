### Title
`DepositAllowlistExtension` gates LP-share recipient (`owner`) instead of the token payer, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` argument (the LP-share recipient) against the per-pool allowlist, but silently ignores the first argument (the actual token payer / `msg.sender` of `addLiquidity`). Because the pool's `addLiquidity` lets the caller freely specify any `owner` address, a non-allowlisted caller can bypass the deposit gate entirely by naming an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` is defined as:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The first `address` parameter — the actual token payer / caller of `addLiquidity` — is unnamed and **never read**. Only `owner` (the LP-share recipient) is checked. The pool's `addLiquidity` interface allows the caller to supply any `owner` address independently of who is paying the tokens. The codebase's own audit-target document explicitly flags this as the "mismatched owner/payer" attack surface and asks whether "a disallowed depositor can still mint LP shares." [2](#0-1) 

The allowlist state is keyed `allowedDepositor[pool][owner]`:

```solidity
mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
``` [3](#0-2) 

Because `owner` is caller-controlled and the payer is never validated, the gate is structurally bypassed.

---

### Impact Explanation

A pool configured with `DepositAllowlistExtension` is intended to be a private liquidity venue — only allowlisted addresses may add liquidity. The bypass breaks this invariant:

- A non-allowlisted attacker calls `pool.addLiquidity(allowlistedAddress, salt, delta, extensionData)`.
- The extension sees `owner = allowlistedAddress` → check passes.
- LP shares are minted to `allowlistedAddress`; tokens are pulled from the attacker.
- If the attacker controls `allowlistedAddress` (e.g., a second wallet they own), they receive LP shares and can later call `removeLiquidity` to recover the tokens — effectively depositing into a private pool with no restriction.
- Even if the attacker does not control `allowlistedAddress`, they can force-inject liquidity into the pool, diluting existing LPs' share of fees and potentially disrupting any stop-loss or oracle-guard extension that relies on per-share metrics.

This is a broken access-control invariant with direct LP-asset impact (unauthorized share minting, fee dilution, guard disruption).

---

### Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidity` directly on the pool.
- Allowlisted addresses are publicly visible on-chain via `AllowedToDepositSet` events.
- The `MetricOmmPoolLiquidityAdder` periphery contract further enables the owner/payer split without any additional barrier. [4](#0-3) 

---

### Recommendation

Change `beforeAddLiquidity` to check the **first parameter** (the actual token payer / caller) rather than — or in addition to — `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    // Gate the economically relevant actor: the token payer.
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to restrict both who pays and who receives shares, check both `sender` and `owner`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached as `extension1` with `beforeAddLiquidity` order set.
2. Admin calls `setAllowedToDeposit(pool, allowlistedAddress, true)` — only `allowlistedAddress` is permitted.
3. Attacker (non-allowlisted EOA) calls:
   ```solidity
   pool.addLiquidity(allowlistedAddress, salt, delta, extensionData);
   ```
4. Pool calls `extension.beforeAddLiquidity(attacker, allowlistedAddress, ...)`.
5. Extension evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` → **no revert**.
6. LP shares are minted to `allowlistedAddress`; tokens are transferred from the attacker.
7. If the attacker controls `allowlistedAddress`, they call `removeLiquidity` and recover the tokens — having successfully deposited into a restricted pool. [1](#0-0) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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

**File:** generate_scanned_questions.py (L647-654)
```python
            short="deposit allowlist gate",
            file_function="metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*",
            call_path="public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner",
            values="the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares",
            control_hint="The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate.",
            validation_focus="Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.",
        ),
```
