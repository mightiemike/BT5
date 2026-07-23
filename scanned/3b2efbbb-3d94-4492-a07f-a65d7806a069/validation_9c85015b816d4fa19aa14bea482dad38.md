### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address as `sender` Instead of End User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` is designed to gate pool swaps by swapper address. However, the guard checks `sender`, which is the direct `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` resolves to the router's address rather than the end user. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the gate to every user who routes through it, fully defeating the per-user allowlist.

---

### Finding Description

The call chain is:

1. **`MetricOmmPool.swap()`** captures `msg.sender` and forwards it as `sender` to `_beforeSwap`: [1](#0-0) 

2. **`ExtensionCalling._beforeSwap()`** encodes that same `sender` value and dispatches it to every configured extension: [2](#0-1) 

3. **`SwapAllowlistExtension.beforeSwap()`** enforces the guard against `sender` — the direct pool caller — not the originating user: [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` inside the pool is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (which is required for any router-based swap to succeed), the check passes unconditionally for every user who routes through it, regardless of whether that user's own address is on the allowlist.

The divergence is structurally identical to the Nomad analog: two values that are supposed to represent the same actor (`sender` = router vs. actual swapper = end user) diverge, and the guard is evaluated against the wrong one.

---

### Impact Explanation

Any pool that simultaneously:
- Deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses, **and**
- Allowlists `MetricOmmSimpleRouter` so that legitimate users can route through it

is fully open to any address that calls through the router. The allowlist provides zero per-user protection. Depending on pool configuration this enables:
- Unauthorized participants draining liquidity from a private or permissioned pool.
- Bypassing KYC/compliance gates that the pool admin believed were enforced.
- LP principal loss if the pool's pricing or depth was calibrated for a specific, trusted counterparty set.

This satisfies the **admin-boundary break** and **broken core pool functionality** impact categories: an unprivileged path (router call) bypasses a configured extension guard with direct fund-impacting consequences.

---

### Likelihood Explanation

The scenario is not hypothetical. Any production deployment that:
1. Configures `SwapAllowlistExtension` on a pool, and
2. Needs the router to work (the standard user-facing entry point)

must allowlist the router. The pool admin has no mechanism to simultaneously allow router-based swaps and enforce per-user restrictions with the current extension design. The conflict is structural and will be encountered by any operator who tries to use both features together.

---

### Recommendation

The extension should check the **end user** rather than the direct pool caller. Two concrete options:

1. **Check `recipient`** — `recipient` is the address that receives output tokens and is passed through the same `beforeSwap` signature (second argument). For router-mediated swaps this is typically the actual user. The extension should check `allowedSwapper[pool][recipient]` instead of `allowedSwapper[pool][sender]`.

2. **Pass end-user identity via `extensionData`** — The router encodes the originating user address into `extensionData`; the extension decodes and verifies it (with a signature or trusted-forwarder pattern).

Option 1 is the minimal fix and consistent with how `recipient` is already threaded through the hook: [4](#0-3) 

---

### Proof of Concept

```
Setup
─────
1. Pool P is deployed with SwapAllowlistExtension E.
2. Admin calls E.setAllowedToSwap(P, router, true)   // router allowlisted so users can route
3. Admin does NOT call E.setAllowedToSwap(P, alice, true)  // alice is NOT allowlisted

Attack
──────
4. Alice calls MetricOmmSimpleRouter.swap(..., pool=P, recipient=alice, ...)
5. Router calls P.swap(recipient=alice, ..., callbackData, extensionData)
   → msg.sender inside P = router
6. P calls _beforeSwap(sender=router, recipient=alice, ...)
7. E.beforeSwap(sender=router, ...) evaluates:
      allowedSwapper[P][router] == true  ✓  → no revert
8. Swap executes. Alice receives tokens from a pool she was explicitly excluded from.
```

The guard never inspects `alice`; it only sees `router`, which is allowlisted. The allowlist is fully bypassed. [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
