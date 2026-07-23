### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool always passes its own `msg.sender` (the direct caller of `pool.swap()`) as `sender`. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the router's address, not the end user's address. A pool admin who allowlists the router to support router-mediated swaps for approved users simultaneously opens the pool to every user on the network, because the extension has no mechanism to distinguish which end user is behind the router call.

---

### Finding Description

**Pool passes `msg.sender` as `sender` to the hook:** [1](#0-0) 

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
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

**Extension checks that `sender` (the direct pool caller) is allowlisted:** [2](#0-1) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` = pool address; `sender` = whoever called `pool.swap()`.

**Router calls `pool.swap()` with itself as `msg.sender`:** [3](#0-2) 

```solidity
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

The router's `msg.sender` (the end user) is stored only in transient callback context for payment purposes — it is never forwarded to the pool as the swap `sender`. The pool sees `msg.sender = router_address` and passes that to the extension.

**The invariant that breaks:**

| Call path | `sender` seen by extension | Allowlist check |
|---|---|---|
| User → `pool.swap()` directly | `user_address` | `allowedSwapper[pool][user_address]` |
| User → `router.exactInputSingle()` → `pool.swap()` | `router_address` | `allowedSwapper[pool][router_address]` |

If the pool admin allowlists the router (a natural configuration so that approved users can use the router), the check degenerates to "is the router allowlisted?" — which is true for every user who routes through it.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a specific set of counterparties (e.g., KYC'd institutions, whitelisted market makers) is fully open to any user who routes through `MetricOmmSimpleRouter`. The pool admin cannot simultaneously:

1. Allow approved users to use the router (by allowlisting the router address), and
2. Prevent non-approved users from swapping (because the router address is the only identity the extension sees).

Unauthorized users gain full swap access to a pool whose LP providers deposited under the assumption that only approved counterparties would trade against them. This exposes LP principal to adversarial flow (arbitrage, MEV, toxic order flow) that the allowlist was designed to exclude — a direct loss-of-LP-value impact.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public entry point for swaps. Any pool admin who wants approved users to enjoy router UX (multi-hop, slippage protection, deadline checks) must allowlist the router. This is the expected production configuration, making the bypass reachable by any unprivileged user with no special setup.

---

### Recommendation

The extension must verify the **end user's identity**, not the intermediary's. Two viable approaches:

1. **Pass end-user identity through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData`; the extension decodes and checks it. This requires the router to cooperate, but the pool admin controls which router is allowlisted and can require a trusted router that always encodes the caller.

2. **Check `recipient` instead of `sender`**: For single-hop swaps where the user is also the recipient, `recipient` carries the end-user address. This does not generalize to multi-hop paths where intermediate recipients are the router itself.

3. **Separate router-allowlist from user-allowlist**: Introduce a two-level check — if `sender` is a known router, additionally verify the payer stored in the router's transient context. This requires a shared interface between the router and the extension.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  pool admin calls: extension.setAllowedToSwap(pool, alice, true)
  pool admin calls: extension.setAllowedToSwap(pool, router, true)
    ↑ necessary so alice can use the router

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  router calls:
    pool.swap(charlie, zeroForOne, amount, limit, "", extensionData)
    // msg.sender in pool = router

  pool calls:
    extension.beforeSwap(router, charlie, ...)
    // sender = router

  extension checks:
    allowedSwapper[pool][router] == true  ✓  → no revert

  Result: charlie swaps successfully in a pool he was never approved for.
``` [4](#0-3) [5](#0-4) [3](#0-2)

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
