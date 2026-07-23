### Title
SwapAllowlistExtension Bypass via Router: Any User Can Swap on Allowlisted Pools When Router Is Allowlisted — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument (which the pool sets to `msg.sender` of the `pool.swap()` call) against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router so that legitimate users can use it, every non-allowlisted user can bypass the individual allowlist by routing through the same router contract.

---

### Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap` checks the caller of `pool.swap`, not the originating user.**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every `beforeSwap` hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that value against the per-pool allowlist, where `msg.sender` inside the extension is the pool: [2](#0-1) 

The effective check is therefore:

```
allowedSwapper[pool][msg.sender_of_pool_swap_call]
```

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(...)` directly. The pool sees `msg.sender` = router address, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The dilemma the admin faces:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the individual allowlist via the router |

There is no configuration that simultaneously (a) lets allowlisted users use the router and (b) blocks non-allowlisted users from using the router.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the economic position owner), which the pool takes directly from the caller's parameter and is not overwritten by the router: [3](#0-2) 

---

### Impact Explanation

Any non-allowlisted address can execute swaps on a pool that the admin intended to restrict to a specific set of swappers, simply by routing through `MetricOmmSimpleRouter`. The swap allowlist — the only on-chain mechanism for restricting who may trade against the pool — is rendered ineffective for all router-mediated swaps. This breaks the admin-boundary invariant: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a configured access-control guard.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard user-facing entry point documented in the protocol.
- Any pool admin who wants allowlisted users to be able to use the router must allowlist the router, which is the natural operational choice.
- Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges.

---

### Recommendation

Pass the originating user's address through the router to the pool, and have the pool forward it to extensions as a separate `originator` field, or require the router to pass the user address in `extensionData` and have `SwapAllowlistExtension` decode and check it. Alternatively, document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this removes the router's utility for restricted pools.

A cleaner fix: change `SwapAllowlistExtension.beforeSwap` to accept the real user address from a trusted `extensionData` payload when `sender` is a known router, or redesign the hook signature so the pool always forwards the transaction originator (`tx.origin` is unsafe; a dedicated `IMetricOmmSwapCallback`-level identity is preferable).

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)      // alice is allowlisted
  admin calls setAllowedToSwap(pool, router, true)     // router allowlisted so alice can use it

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle(pool, ...)

  Router calls:
    pool.swap(recipient=bob, ...)   // msg.sender = router

  Pool calls:
    _beforeSwap(sender=router, ...)

  SwapAllowlistExtension checks:
    allowedSwapper[pool][router] == true  → PASSES

  Result: bob swaps successfully despite not being on the allowlist.

Direct call by bob:
  pool.swap(recipient=bob, ...)   // msg.sender = bob
  allowedSwapper[pool][bob] == false → REVERTS NotAllowedToSwap

  → The allowlist only blocks direct calls, not router-mediated calls.
``` [2](#0-1) [1](#0-0)

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
