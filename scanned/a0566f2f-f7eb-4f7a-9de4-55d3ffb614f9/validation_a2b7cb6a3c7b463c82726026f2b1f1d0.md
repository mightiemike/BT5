### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted — not whether the **actual user** is allowlisted. If the router is added to the allowlist (a natural admin action to enable router-mediated swaps for curated pools), every user on the network can bypass the swap allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value and dispatches it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` receives that `sender` and checks it against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*(...)`, the router is the direct caller of `pool.swap()`. Therefore `sender = router_address`, and the extension evaluates `allowedSwapper[pool][router_address]` — completely ignoring the actual end-user. The allowlist is keyed on the wrong actor.

Two harmful outcomes follow:

1. **Bypass (High impact):** A pool admin allowlists the router so that their curated users can trade through the standard periphery. This single entry makes the allowlist meaningless: every non-allowlisted address on the network can now swap on the curated pool simply by calling the router.

2. **Broken functionality (Medium impact):** A pool admin does *not* allowlist the router. Allowlisted users are then silently blocked from using the router and must call `pool.swap()` directly — an undocumented and unexpected restriction that breaks the intended UX.

The analog to the external C-02 report is exact: in C-02 an `accountAddress` parameter is used instead of `msg.sender`, letting anyone impersonate the intended actor. Here, the `sender` parameter carries the router's address instead of the real user's address, letting anyone impersonate an allowlisted swapper.

---

### Impact Explanation

**Severity: High**

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses loses that protection entirely for all router-mediated swaps. Any non-allowlisted user can execute swaps on the pool, defeating the curation policy. This is a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a configured access-control guard with no special preconditions beyond the router being allowlisted.

---

### Likelihood Explanation

**Likelihood: High**

The `MetricOmmSimpleRouter` is the canonical, documented entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. This is the natural and expected configuration. Once the router is allowlisted, the bypass is unconditional and requires no privileged access, no special token, and no multi-step setup — any address can call the router.

---

### Recommendation

The extension must gate on the **actual end-user**, not on the intermediate contract. Two approaches:

1. **Pass the original caller through the router.** Have `MetricOmmSimpleRouter` encode the real `msg.sender` inside `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it. This requires the extension to trust the pool's router identity.

2. **Check `sender` (the router) and require the router to attest the real user.** Define a `ISwapRouter` interface that exposes the current user, and have the extension call back into the router to retrieve the real caller.

3. **Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps** and enforce this at pool-creation time (e.g., revert if both the router and the allowlist extension are configured together without an explicit override).

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension as extension1, beforeSwap order = 1
  admin = pool admin
  alice = allowlisted user
  bob   = non-allowlisted user
  router = MetricOmmSimpleRouter (deployed, known address)

Step 1 – Admin configures the allowlist:
  admin calls SwapAllowlistExtension.setAllowedToSwap(pool, alice, true)
  admin calls SwapAllowlistExtension.setAllowedToSwap(pool, router, true)
    ↳ Admin adds the router so that alice can trade through the standard periphery.

Step 2 – Bob (non-allowlisted) calls pool.swap() directly:
  pool.swap(bob_recipient, ...) from bob
    → beforeSwap receives sender=bob
    → allowedSwapper[pool][bob] == false → REVERT ✓ (guard works for direct calls)

Step 3 – Bob routes through MetricOmmSimpleRouter:
  router.exactInput({path: [token0, pool, token1], recipient: bob, ...}) from bob
    → router calls pool.swap(bob_recipient, ...) with msg.sender = router
    → beforeSwap receives sender=router
    → allowedSwapper[pool][router] == true → PASS ✗ (guard bypassed)
    → Bob's swap executes on the curated pool despite not being allowlisted.
``` [4](#0-3) [5](#0-4)

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
