### Title
SwapAllowlistExtension Checks the Router Address Instead of the Real Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is used, `msg.sender` to the pool is the **router contract**, not the real user. If the router is allowlisted (which is required for any router-based swap to succeed), every user on the internet can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to the extension hook:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is allowlisted:

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

`MetricOmmSimpleRouter.exactInputSingle()` / `exactInput()` / `exactOutputSingle()` / `exactOutput()` all call `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L104-112
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
      i == last ? params.recipient : address(this),
      zeroForOne,
      amount,
      ...
    );
```

When the router calls `pool.swap()`, the pool sees `msg.sender == router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][realUser]`. A pool admin who allowlists the router (to support router-based swaps) inadvertently grants every user the ability to swap, defeating the allowlist entirely.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted institutions) provides **zero protection** once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and swap against the restricted pool. The pool's LP assets are exposed to unrestricted trading by actors the pool admin explicitly intended to exclude. This is a direct loss-of-curation-policy failure with fund-impacting consequences (LPs bear the risk of trading with unintended counterparties, including adversarial actors who may exploit oracle-price windows).

---

### Likelihood Explanation

The likelihood is **high**. The router is the canonical, documented periphery entry point for swaps. Any pool that:
1. Deploys `SwapAllowlistExtension` to restrict swaps, **and**
2. Allowlists the router so that legitimate users can trade via the standard UI/router path

...is fully bypassed. These two conditions are the normal operating configuration for a curated pool that still wants to support router-based UX. The pool admin has no way to simultaneously allowlist the router for legitimate users and block illegitimate users, because the extension cannot distinguish between them once the router is the `sender`.

---

### Recommendation

The extension must check the **real economic actor**, not the intermediary. Two complementary fixes:

1. **Pass the real user through the router**: The router should forward the original `msg.sender` as part of `extensionData`, and the extension should decode and check it. Alternatively, the pool interface could accept an explicit `swapper` argument distinct from `msg.sender`.

2. **Check `recipient` or require the router to attest the caller**: The extension could require that `extensionData` contains a signed or router-attested caller identity, and verify it against the allowlist.

The simplest safe fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and check that address instead of (or in addition to) the `sender` argument.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for legitimate users.
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: attacker, ...})`.
4. Router calls `pool.swap(attacker, ...)` — pool sees `msg.sender == router`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Attacker successfully swaps against the restricted pool without being individually allowlisted.

The check that should have blocked the attacker (`allowedSwapper[pool][attacker]`) is never evaluated.