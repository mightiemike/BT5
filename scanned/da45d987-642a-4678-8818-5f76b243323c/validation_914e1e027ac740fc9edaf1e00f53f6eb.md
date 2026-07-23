### Title
SwapAllowlistExtension gates the router address instead of the actual user, making the allowlist incompatible with MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore gates the router's address, not the individual user's address. This creates a binary failure: either allowlisted users cannot use the router at all, or the pool admin allowlists the router to enable router-mediated swaps, which silently opens the allowlist to every user.

---

### Finding Description

**Step 1 — Extension check**

`SwapAllowlistExtension.beforeSwap` gates access by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool: [1](#0-0) 

**Step 2 — Pool passes its own `msg.sender` as `sender`**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, so `sender` is whoever called `pool.swap()`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first positional argument to the extension: [3](#0-2) 

**Step 3 — Router is the direct caller of `pool.swap()`**

Every router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly. The pool's `msg.sender` is therefore the router contract, not the end user: [4](#0-3) 

The router stores the original user only in transient callback context for payment purposes; it is never forwarded to the pool as the swap initiator: [5](#0-4) 

**Result**: The extension's `sender` argument is always the router address for any router-mediated swap. The extension has no visibility into the actual end user.

---

### Impact Explanation

Two mutually exclusive failure modes arise for any pool that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter`:

**Mode A — Router not allowlisted (default)**
The pool admin allowlists individual user addresses (e.g., Alice, Bob). The router is not on the list. Every router-mediated swap reverts with `NotAllowedToSwap` regardless of whether the user is allowlisted. Allowlisted users are locked out of the primary user-facing swap interface. This is broken core swap functionality.

**Mode B — Router allowlisted (to restore router access)**
The pool admin adds the router to `allowedSwapper[pool]` so that allowlisted users can reach the pool through the router. Because the extension checks the router's address and the router is now approved, every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through `MetricOmmSimpleRouter`. The curation policy is silently nullified.

In Mode B, a non-allowlisted user (e.g., a sanctioned address, an unverified counterparty, or a competitor the pool admin intended to exclude) can execute swaps against the pool's LP liquidity. The LP's exposure to unwanted counterparties is the direct fund-impacting consequence.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented in the periphery. Any production pool that deploys `SwapAllowlistExtension` to restrict access will encounter this incompatibility the first time a legitimate user attempts a router-mediated swap. The pool admin's natural remediation (allowlisting the router) immediately triggers Mode B. The trigger requires no privileged attacker capability — any public user with a wallet can call `exactInputSingle`.

---

### Recommendation

The router should forward the original user's address to the pool so the extension can gate on the correct actor. Two viable approaches:

1. **Pass originator in `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. `SwapAllowlistExtension.beforeSwap` decodes and checks this address when `sender` is a known router.

2. **Dedicated originator field in `pool.swap()`**: Add an `originator` parameter to `pool.swap()` that the pool passes to extensions as a separate argument, distinct from `sender`. The router sets `originator = msg.sender`; direct callers set `originator = address(0)` (falling back to `sender`).

Either approach must be authenticated (e.g., only trusted routers may set a non-zero originator) to prevent a malicious caller from spoofing an allowlisted address.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in beforeSwap order
  admin allowlists Alice:  allowedSwapper[pool][alice] = true
  admin allowlists router: allowedSwapper[pool][router] = true
    (necessary so Alice can use the router)

Attack:
  Bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

Execution trace:
  router.exactInputSingle()          msg.sender = bob
    pool.swap(recipient=bob, ...)    msg.sender = router
      _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router, ...)
          allowedSwapper[pool][router] == true  → PASSES
      swap executes, bob receives tokens

Result:
  Bob swaps successfully on a pool he was explicitly excluded from.
  The allowlist is bypassed without any privileged action.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
