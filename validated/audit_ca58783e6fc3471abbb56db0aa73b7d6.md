### Title
SwapAllowlistExtension Checks the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. If the pool admin allowlists the router (required for any router-based swap to succeed), every user who routes through it bypasses the per-user allowlist, regardless of whether they are individually permitted.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to the `beforeSwap` extension hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [2](#0-1) 

When a user calls the pool directly, `sender` equals the user — the check is correct. When the same user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool call, so `sender` equals the **router address**. The extension never sees the actual end user.

The pool admin faces an impossible choice:

| Admin configuration | Direct call by non-allowlisted user | Router call by non-allowlisted user |
|---|---|---|
| Router NOT allowlisted | Blocked ✓ | Blocked ✓ (but allowlisted users also cannot use the router) |
| Router allowlisted | Blocked ✓ | **Allowed ✗ — bypass** |

Once the router is allowlisted to enable router-based swaps for legitimate users, the allowlist is effectively nullified for all users who route through it.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position beneficiary), which is passed explicitly and is not overwritten by the router: [3](#0-2) 

The swap extension, by contrast, relies on the transient identity of the immediate caller rather than the economic actor the policy is meant to gate.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps, draining LP value at oracle-derived prices that the pool admin intended to reserve for specific parties. This is a direct loss of LP principal and a broken core pool invariant (curation policy).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported periphery path for multi-hop swaps. Any pool admin who enables router-based swaps for allowlisted users must allowlist the router, which simultaneously opens the bypass for all other users. The trigger requires no privileged access, no malicious setup, and no non-standard tokens — only a standard router call.

---

### Recommendation

The extension must identify the actual end user, not the immediate caller. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes the originating user address in `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply honest data, which is acceptable since the router is a known, audited contract.

2. **Check `recipient` instead of `sender`**: For swap allowlists, the economic actor receiving value is `recipient`. The pool already passes `recipient` as the second argument to `beforeSwap` (currently unnamed/ignored in the extension). Gating on `recipient` prevents non-allowlisted addresses from receiving swap output regardless of routing path.

The simplest production fix is to replace the `sender` check with a `recipient` check in `beforeSwap`:

```solidity
// current (wrong actor)
function beforeSwap(address sender, address, ...)
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) { ... }

// fixed (correct actor)
function beforeSwap(address, address recipient, ...)
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) { ... }
```

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin allowlists `userA` and the `MetricOmmSimpleRouter` address (necessary for router-based swaps).
3. `userB` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the curated pool.
4. The router calls `pool.swap(recipient=userB, ...)` — `msg.sender` of the pool call is the router.
5. `beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `userB` receives output tokens from the curated pool despite never being individually allowlisted. [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
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
