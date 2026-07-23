### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Recipient (`owner`) Instead of Actual Depositor — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces the curated-pool deposit gate by checking whether the `owner` argument (the LP-position recipient) is on the allowlist. It never checks the first unnamed `address` parameter — the actual `sender` who is calling `addLiquidity` and supplying the tokens. Any non-allowlisted address can bypass the gate by nominating an allowlisted address as `owner`.

---

### Finding Description

The allowlist storage is keyed by `[pool][depositor]` and is populated by the pool admin:

```solidity
// DepositAllowlistExtension.sol
mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;

function setAllowedToDeposit(address pool_, address depositor, bool allowed)
    external onlyPoolAdmin(pool_)
{
    allowedDepositor[pool_][depositor] = allowed;   // keyed on the intended depositor
}
``` [1](#0-0) 

The hook that enforces this policy at deposit time is:

```solidity
function beforeAddLiquidity(
    address,          // ← first param: the actual sender/caller of addLiquidity (IGNORED)
    address owner,    // ← second param: the LP-position recipient
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

The guard checks `allowedDepositor[pool][owner]` — i.e., whether the LP-position *recipient* is allowlisted — but never inspects the first unnamed `address` parameter, which is the address that actually called `addLiquidity` on the pool and is supplying the tokens. This is the direct analog of the ERC721 bug: the wrong actor is bound to the authorization check.

**Attack path:**

1. Non-allowlisted address `A` identifies any allowlisted address `B` (e.g., a known LP or the pool admin themselves).
2. `A` calls `pool.addLiquidity(owner = B, ...)` directly, supplying tokens.
3. The pool invokes `beforeAddLiquidity(sender=A, owner=B, ...)`.
4. The extension evaluates `allowedDepositor[pool][B]` → `true`; the revert is never triggered.
5. `A`'s tokens enter the curated pool; the LP position is minted to `B`.

The first unnamed parameter (the real depositor `A`) is silently discarded.

---

### Impact Explanation

The deposit allowlist is the primary access-control boundary for curated pools. Bypassing it allows any unprivileged address to inject liquidity into a pool whose admin explicitly restricted depositors. Consequences include:

- **Compliance/KYC bypass**: non-whitelisted counterparties deposit into regulated pools.
- **Pool-state manipulation**: an adversary can shift bin liquidity distribution, affecting oracle-derived bid/ask spreads and swap pricing for all subsequent traders.
- **LP dilution**: existing allowlisted LPs' share of fees and principal is diluted by unauthorized deposits.

This meets the "broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" and "admin-boundary break" impact criteria.

---

### Likelihood Explanation

- The call is permissionless: any EOA or contract can call `pool.addLiquidity` directly with an arbitrary `owner`.
- No special privilege, flash loan, or oracle manipulation is required.
- The allowlisted `owner` address can be any publicly known LP (e.g., read from on-chain events).
- Likelihood: **High**.

---

### Recommendation

Replace the `owner` check with the first unnamed parameter (the actual sender/depositor). Rename it for clarity:

```solidity
function beforeAddLiquidity(
    address sender,   // the address that called addLiquidity — the real depositor
    address owner,
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the intent of `setAllowedToDeposit`: the depositor who supplies tokens must be allowlisted, regardless of who receives the LP position.

---

### Proof of Concept

```solidity
// Assume pool uses DepositAllowlistExtension.
// Admin has allowlisted only `alice`; `attacker` is NOT allowlisted.

address alice   = 0xAlice;   // allowlisted
address attacker = 0xBad;    // NOT allowlisted

// Attacker calls addLiquidity with owner = alice.
// Extension checks allowedDepositor[pool][alice] → true → no revert.
pool.addLiquidity(
    alice,          // owner (LP recipient) — allowlisted, check passes
    binId,
    liquidityDelta,
    extensionData
);
// Attacker's tokens are now in the curated pool.
// LP position minted to alice, but attacker has bypassed the deposit gate.
``` [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-20)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
