### Title
`DepositAllowlistExtension` Guards `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook silently drops the `sender` parameter and enforces the allowlist only against `owner` (the LP-share recipient). Because `addLiquidity` lets any `msg.sender` specify an arbitrary `owner`, any unprivileged address can deposit tokens into a restricted pool by naming an already-allowlisted address as `owner`, bypassing the guard entirely.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses to the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

- `sender` = `msg.sender` — the caller who will supply tokens via the swap-callback.
- `owner` = caller-supplied parameter — the address that receives LP shares.

`DepositAllowlistExtension.beforeAddLiquidity` explicitly discards `sender` (unnamed first argument) and enforces the allowlist only on `owner`: [2](#0-1) 

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

The contract's own NatSpec states the intent: *"Gates `addLiquidity` by depositor address, per pool."* The depositor is `sender` — the entity that actually transfers tokens. `owner` is merely the LP-share recipient and is freely chosen by the caller.

Compare with `SwapAllowlistExtension`, which correctly checks `sender`: [3](#0-2) 

```solidity
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

The asymmetry is the root cause: the swap guard checks the right actor; the deposit guard checks the wrong one.

---

### Impact Explanation

An unprivileged address that is **not** on the allowlist can:

1. Call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)`.
2. The `beforeAddLiquidity` hook evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` → passes.
3. The attacker's `metricOmmAddLiquidityCallback` (or any compatible callback) transfers tokens into the pool.
4. LP shares are minted to `allowlistedAddress`, not the attacker.

Consequences:
- **Allowlist invariant broken**: the pool admin's configured access control is silently bypassed by any caller.
- **Unwanted LP exposure forced on `owner`**: the allowlisted address receives LP shares it never requested, gaining involuntary exposure to pool risk and requiring an active `removeLiquidity` call to exit.
- **Attacker loses deposited tokens** (transferred to pool, shares credited elsewhere) — but this is the mechanism of the bypass, not a deterrent, since the attacker may be acting on behalf of a colluding `owner`.
- **Compliance/KYC pools broken**: pools deployed specifically to restrict participation to vetted LPs are rendered open to any depositor.

---

### Likelihood Explanation

- No special role or privilege is required — any EOA or contract can call `addLiquidity`.
- The only prerequisite is knowing one allowlisted address for the target pool, which is readable from `allowedDepositor` (public mapping) or from on-chain events.
- The bypass is a single direct call; no flash loan, oracle manipulation, or multi-step setup is needed.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual token depositor) instead of `owner`:

```solidity
// current (wrong):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) { ... }

// fixed:
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) { ... }
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

```solidity
// Assume pool has DepositAllowlistExtension configured.
// allowedDepositor[pool][alice] = true; attacker is NOT allowlisted.

address attacker = address(0xBEEF);
address alice    = address(0xA11CE); // allowlisted

vm.startPrank(attacker);

// Direct call: attacker names alice as owner.
// beforeAddLiquidity checks allowedDepositor[pool][alice] == true → passes.
// Attacker's callback transfers tokens; alice receives LP shares.
pool.addLiquidity(
    alice,          // owner — allowlisted, check passes
    0,              // salt
    deltas,
    callbackData,   // attacker's callback transfers tokens
    ""
);

// Result: attacker bypassed the deposit allowlist.
// Alice now holds LP shares she never requested.
vm.stopPrank();
```

The guard on `sender` that `SwapAllowlistExtension` enforces is entirely absent from `DepositAllowlistExtension`, making the deposit allowlist bypassable by any unprivileged caller who can name an allowlisted `owner`.

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
