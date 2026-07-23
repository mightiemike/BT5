### Title
`SwapAllowlistExtension` Allowlist Fully Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool passes in. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` the extension sees — is the **router contract**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unpermissioned user can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Pool → Extension argument binding**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the configured extension: [2](#0-1) 

**What the allowlist actually checks**

`SwapAllowlistExtension.beforeSwap` gates on `sender` — the first argument — keyed against the pool (`msg.sender` of the extension call): [3](#0-2) 

**The mismatch**

When a user calls `pool.swap()` directly, `sender = user EOA` → the allowlist correctly gates the user.

When a user calls `MetricOmmSimpleRouter.exact*()`, the router calls `pool.swap()`, so `sender = router address`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — including non-allowlisted ones — bypasses the gate by routing through the router |

The admin's only path to supporting router-mediated swaps is to allowlist the router, which silently opens the gate to the entire public.

The `isAllowedToSwap` view function and the `setAllowedToSwap` admin setter both operate on the `swapper` address: [4](#0-3) 

There is no mechanism to recover the original end-user identity once the router is the direct caller of `pool.swap()`.

---

### Impact Explanation

**Severity: Medium**

The swap allowlist invariant — "only allowlisted addresses may swap in this pool" — is completely broken for any pool whose admin allowlists the router. Any unpermissioned user can execute swaps against a supposedly restricted pool by routing through `MetricOmmSimpleRouter`. This is a broken core pool access-control mechanism with direct fund-flow consequences (unauthorized parties can drain liquidity via swaps).

---

### Likelihood Explanation

**High.** The router is the standard periphery entry point for multi-hop and slippage-protected swaps. A pool admin who wants to restrict swaps to a curated set of users but still allow those users to use the router (the normal UX path) will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `MetricOmmSimpleRouter.exact*()`.

---

### Recommendation

The extension must gate on the **end user's identity**, not the intermediary's. Two viable approaches:

1. **Pass end-user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's claim, which is only safe if the router itself is a verified, non-upgradeable contract.

2. **Check `sender` at the router level before calling the pool**: The router maintains its own allowlist and reverts before forwarding to the pool. The pool-level extension then only needs to gate direct pool callers.

Either way, the current design where `sender = router address` for all router-mediated swaps makes per-user gating impossible once the router is allowlisted.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended gated user
3. Admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
4. charlie (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInput(
           pool, zeroForOne, amount, priceLimit, callbackData, extensionData
       )
5. Router calls pool.swap(recipient, ...) with msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension checks: allowedSwapper[pool][router] == true  → PASSES.
8. Charlie's swap executes against the restricted pool.
```

The bypass requires zero special access — only a public router call. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-29)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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
