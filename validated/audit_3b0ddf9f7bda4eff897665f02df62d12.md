### Title
SwapAllowlistExtension checks router's identity instead of the actual swapper, allowing any user to bypass the pool's swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating user. If the router is allowlisted (which is required for any legitimate router-mediated swap to work on a restricted pool), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// ExtensionCalling.sol – _beforeSwap
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
            sender,      // ← pool's msg.sender, i.e. the router
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
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```
allowAll[pool] || allowedSwapper[pool][sender]
```

where `sender` is the router's address, not the originating EOA.

**Attack path (two-step, no privilege required):**

1. A pool admin deploys a pool with `SwapAllowlistExtension`, sets `allowAll[pool] = false`, and allowlists a set of approved EOAs. To let those EOAs use the router, the admin also adds the router to `allowedSwapper[pool]`.
2. A non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any `exact*` entry point). The router calls `pool.swap(...)` with itself as `msg.sender`. The extension evaluates `allowedSwapper[pool][router] == true` and passes. The swap executes in full.

The pool admin cannot resolve this without removing the router from the allowlist, which simultaneously breaks router-mediated swaps for every legitimate user. There is no way to distinguish "router acting on behalf of an approved user" from "router acting on behalf of an unapproved user" at the extension layer, because the originating caller's identity is never forwarded.

The structural parallel to M-08 is exact: the guard (`validateVariablePoolHasEnoughLiquidity` / `SwapAllowlistExtension.beforeSwap`) performs a check that passes, but the check is on the wrong entity — the accounting token holder / the router — rather than the entity whose access the invariant is meant to control. In both cases the invariant is satisfied on paper while the intended protection is silently absent.

---

### Impact Explanation

Any unprivileged user can swap on a pool that the admin intended to restrict to a specific set of addresses. Depending on the pool's purpose (KYC-gated, institutional-only, whitelist-launch), this breaks the core access-control invariant and allows unauthorized parties to extract value from the pool's liquidity at oracle-derived prices. This is a broken core pool functionality finding with direct fund-impact potential.

---

### Likelihood Explanation

The trigger is a standard public router call — no special role, no flash loan, no multi-block setup. The only precondition is that the router is allowlisted on the pool, which is the natural and necessary configuration for any pool that wants to support router-mediated swaps alongside the allowlist. The likelihood is **medium-high**: the misconfiguration is the expected operational state, not an edge case.

---

### Recommendation

The router must forward the originating caller's address to the pool so the extension can gate on the real user. Two options:

1. **Pass `msg.sender` through the router.** Add a `swapper` field to the swap parameters struct and have the router populate it with `msg.sender` before calling `pool.swap()`. The pool then passes this field as `sender` to extensions instead of its own `msg.sender`.
2. **Check `tx.origin` in the extension (not recommended for general use).** Only acceptable in controlled environments where `tx.origin` is guaranteed to be the economic actor.

Option 1 is the correct fix. It mirrors the pattern used by Uniswap v4's `PoolManager`, which passes the original caller through the unlock/callback chain so hooks always see the true initiator.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  allowAll[pool] = false
  allowedSwapper[pool][alice] = true          // alice is approved
  allowedSwapper[pool][router] = true         // router added so alice can use it

Attack:
  bob (not in allowedSwapper) calls:
    router.exactInputSingle({pool: pool, ...})

  router calls:
    pool.swap(recipient=bob, ...)              // msg.sender = router

  SwapAllowlistExtension.beforeSwap receives:
    sender = router
    allowedSwapper[pool][router] == true  →  check passes

  Result: bob's swap executes on a pool he is not authorized to use.
  alice cannot use the router without re-enabling the router allowlist entry,
  and removing the router entry blocks bob but also blocks alice.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L319-332)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();

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
