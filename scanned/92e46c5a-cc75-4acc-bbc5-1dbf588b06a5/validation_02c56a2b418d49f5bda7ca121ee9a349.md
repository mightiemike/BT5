### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged caller to bypass per-user swap access control via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. The allowlist therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][endUser]`. If the pool admin allowlists the router (the only way to permit any router-mediated swap for legitimate users), every unprivileged address can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that `sender`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router contract: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. There is no code path that threads the original `msg.sender` of the router call through to the extension.

This creates an irresolvable identity mismatch structurally identical to the external report's `_totalVoteWeight` inflation: just as invalid pools silently dilute the weight divisor without reverting, the router silently substitutes its own address for the end user's address without reverting, making the configured guard evaluate the wrong principal.

The pool admin faces a binary, broken choice:

| Admin action | Effect |
|---|---|
| Allowlist specific users only | Those users are blocked when they use the router (router not allowlisted) |
| Allowlist the router | Every address on the network can swap by routing through the router |

There is no configuration that enforces per-user access control for router-mediated swaps.

---

### Impact Explanation

**High.** A pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) cannot enforce that restriction for any user who routes through `MetricOmmSimpleRouter`. Once the admin allowlists the router to unblock legitimate users, every unprivileged address can execute swaps against the pool. Depending on pool configuration, this enables:

- Unauthorized extraction of LP value at oracle-anchored prices by non-allowlisted actors.
- Circumvention of regulatory or contractual access controls, exposing the pool operator to liability.
- Draining of concentrated liquidity bins by actors the pool was explicitly designed to exclude.

The pool's core swap functionality is broken in the sense that the configured guard does not protect the intended invariant.

---

### Likelihood Explanation

**Medium.** The trigger requires the pool admin to allowlist the router — a natural, expected action for any pool that wants to support the standard periphery. The admin has no indication from the contract or documentation that doing so opens the gate to all users. The bypass is then reachable by any unprivileged address with no further preconditions.

---

### Recommendation

Pass the original end-user identity through the call chain so the extension can gate on it. Two complementary approaches:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it (requires the extension to trust the router, which can be enforced by checking `sender == trustedRouter`).

2. **Pool-side**: Add an `originator` field to the `swap` call signature that the router populates with its own `msg.sender`, and pass it to extensions alongside `sender`. Extensions can then choose which identity to gate on.

Until fixed, pools that require per-user swap access control must not allowlist the router and must instruct users to call `pool.swap()` directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only intended swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // admin does this to let alice use the router

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - Bob extracts tokens from the pool at oracle-anchored prices.
  - Alice's per-user allowlist entry is irrelevant for router-mediated swaps.
  - The SwapAllowlistExtension guard is silently bypassed.
``` [5](#0-4) [4](#0-3) [1](#0-0)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
