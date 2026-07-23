### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks the router's allowlist status rather than the actual user's. If the router is allowlisted (a natural admin action for a "trusted periphery"), every user on the internet can bypass the curated pool's swap restriction by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`msg.sender` here is the pool (the pool calls the extension), and `sender` is whatever address the pool received as the caller of `pool.swap()`. When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap()` on the user's behalf, so the pool sees `msg.sender = router` and forwards `sender = router` to the extension hook. [2](#0-1) 

The allowlist is therefore evaluated against the router's address, not the actual user's address. A pool admin who allowlists the router (a natural and expected action for a "trusted periphery contract") inadvertently opens the gate to every user who can call the public router.

The `DepositAllowlistExtension` does not share this exact flaw because it checks `owner` (the LP recipient), which the liquidity adder passes as the actual user. The swap path has no equivalent forwarding of the real initiator. [3](#0-2) 

---

### Impact Explanation

A curated pool that uses `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned contracts, or whitelisted market makers) is fully bypassed. Any unprivileged user can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`. This constitutes a broken core pool functionality and a direct admin-boundary break: the pool admin's curation policy is silently nullified, and LP funds are exposed to unrestricted trading from actors the pool was explicitly designed to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. The bypass requires only that the router address is allowlisted on the pool — a step a pool admin would naturally take to allow their own users to trade through the supported periphery. No privileged access, no special tokens, and no malicious setup are required. The attacker's only action is calling the public router. [4](#0-3) 

---

### Recommendation

The extension must gate on the economically relevant actor — the address that initiated the transaction — not the intermediate contract that called the pool. Two sound approaches:

1. **Pass `tx.origin` as the checked identity** (acceptable only if the pool's threat model excludes contract callers, which is a strong assumption).
2. **Require the router to forward the real initiator** and have the pool pass that forwarded address as `sender` to the extension, rather than using `msg.sender` of the pool call.
3. **Do not allowlist the router**; instead, require all allowlisted users to call the pool directly. Document this constraint explicitly so pool admins do not inadvertently allowlist the router.

The cleanest fix is option 2: the pool's `swap` entrypoint should accept an explicit `originator` field that the router populates with `msg.sender` before calling the pool, and the extension should check that field.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin allowlists the router
  allowedSwapper[pool][attacker] = false // attacker is NOT allowlisted

Attack:
  attacker calls MetricOmmSimpleRouter.exactInput(pool, ...)
  router calls pool.swap(sender=router, ...)
  pool calls extension.beforeSwap(sender=router, ...)
  extension checks allowedSwapper[pool][router] == true  → passes
  attacker's swap executes against restricted LP funds
```

The extension's check at line 37 evaluates `allowedSwapper[msg.sender][sender]` where `sender = router`, not `attacker`. The guard fails open for every user who routes through the public router. [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-14)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

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
