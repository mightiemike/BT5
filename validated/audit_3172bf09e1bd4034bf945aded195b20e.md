### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool. The extension therefore checks whether the **router** is allowlisted, not the **end user**. A pool admin who allowlists the router to enable router-mediated swaps for permitted users simultaneously opens the pool to every unpermitted user who routes through the same public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument:

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
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is the value the pool passes as the first argument to `_beforeSwap`, which is `msg.sender` of the pool's own `swap()` call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end user
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

When a user calls `MetricOmmSimpleRouter.exactInput*()`, the router calls `pool.swap()`. The pool's `msg.sender` is the **router contract**, so `sender` passed to the extension is the router's address. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the router at all; they must call `pool.swap()` directly.
- **Allowlist the router** → every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`, because the router's address passes the check regardless of who initiated the call.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional partners, or whitelisted protocols) is fully bypassed by any unpermitted user who calls `MetricOmmSimpleRouter`. The attacker receives output tokens from the pool's liquidity at oracle-derived prices, draining LP value without authorization. This breaks the core curation invariant of the allowlist extension and constitutes a direct loss of LP assets to unauthorized swap execution.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` or `exactInput` targeting any pool. No special privilege, flash loan, or setup is required. The bypass is reachable on every swap-allowlisted pool the moment the admin allowlists the router (which is the only way to let permitted users use the router). Likelihood is **High**.

---

### Recommendation

The extension must recover the original end user rather than trusting the `sender` parameter, which reflects only the direct caller of `pool.swap()`. Two approaches:

1. **Short term:** Require that swaps on allowlisted pools are made directly against the pool (i.e., `msg.sender == tx.origin` or a dedicated router that forwards the originating user address in `extensionData`). The extension can decode the real user from `extensionData` when a trusted router is the `sender`.

2. **Long term:** Redesign the router to pass the originating user's address in `extensionData` and have `SwapAllowlistExtension` verify both that `sender` is a trusted router and that the decoded user is allowlisted. This preserves composability while correctly gating the economically relevant actor.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension as beforeSwap hook
  - Admin calls setAllowedToSwap(pool, alice, true)       // Alice is permitted
  - Admin calls setAllowedToSwap(pool, router, true)      // Router allowlisted so Alice can use it
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)

Execution trace:
  MetricOmmSimpleRouter.exactInputSingle()
    → pool.swap(recipient=Bob, ...) [msg.sender = router]
    → MetricOmmPool._beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓  (passes)
    → swap executes, Bob receives output tokens

Result:
  Bob, who is not allowlisted, successfully swaps against the curated pool.
  The allowlist check evaluated the router's address (allowlisted) instead of Bob's address (not allowlisted).
  Any user can repeat this to drain LP assets from the restricted pool.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

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
