### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's own `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, that value is the **router address**, not the originating user. A pool admin who allowlists the router to support router-mediated swaps simultaneously grants every user on the network the ability to bypass the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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
``` [1](#0-0) 

The pool populates that `sender` slot with its own `msg.sender` — visible in `simulateSwapAndRevert` and the live `swap` path through `_beforeSwap`:

```solidity
// MetricOmmPool.sol L321-331
_beforeSwap(
  msg.sender,   // ← becomes `sender` in the extension
  recipient,
  zeroForOne,
  ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter` (the supported periphery entry point), the router calls `pool.swap(...)` on the user's behalf. At that point `msg.sender` inside the pool is the **router contract**, so `sender` delivered to the extension is the router address, not the originating user.

The allowlist check therefore becomes:

```
allowedSwapper[pool][router]   // checked
allowedSwapper[pool][user]     // never checked
```

A pool admin faces an impossible choice:

| Admin decision | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps revert for **everyone**, including legitimately allowlisted users |
| **Allowlist the router** | Every user on the network can bypass the allowlist by routing through the router |

The second branch is the exploit path. Because `MetricOmmSimpleRouter` is the documented, supported swap entry point, any production curated pool that wants to support router users must allowlist it, and doing so silently opens the gate to all callers. [3](#0-2) 

---

### Impact Explanation

Any user excluded from a curated pool's allowlist can execute swaps on that pool by routing through `MetricOmmSimpleRouter`. The swap executes at full oracle-anchored prices and settles real token transfers. The pool admin's curation policy (KYC gate, institutional-only pool, partner-only liquidity) is completely nullified. This is a direct, fund-impacting bypass of a core access-control mechanism with no loss threshold — every swap that should have been blocked goes through.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented in the periphery.
- Any pool admin who deploys a `SwapAllowlistExtension` and wants legitimate users to swap through the router **must** allowlist the router, triggering the vulnerability automatically.
- No privileged access, no special token, no malicious setup is required — a standard router call suffices.
- The bypass is deterministic and repeatable on every block.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two complementary approaches:

1. **Pass the original caller through the router.** The router should forward the originating `msg.sender` as an explicit `sender` field in `extensionData` (authenticated by the pool's callback context), and the extension should decode and verify it.

2. **Check `tx.origin` as a fallback** (acceptable only if the threat model excludes contract callers; otherwise use approach 1).

3. **Alternatively**, the pool's `_beforeSwap` dispatcher could accept an explicit `sender` override from a trusted router callback rather than always using `msg.sender`.

The `DepositAllowlistExtension` does not share this exact bug because it keys on `owner` (the LP position recipient), which the liquidity adder passes as the user-supplied value rather than its own address. [4](#0-3) 

---

### Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension attached.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   → alice is allowlisted.
3. Pool admin calls setAllowedToSwap(pool, router, true)  → router allowlisted so alice can use it.
   (Without this step, alice cannot use the router either.)

Attack
──────
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...) targeting the pool.
5. Router calls pool.swap(recipient=bob, ...).
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks: allowedSwapper[pool][router] == true  → passes.
8. Swap executes; Bob receives tokens from the curated pool.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
```

The root cause is identical in structure to the AtlasVerification analog: a trusted intermediary (`isSimulation` flag / router address) causes the guard to evaluate a proxy identity instead of the actual economic actor, making the guard trivially bypassable on every real user flow. [5](#0-4) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
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
