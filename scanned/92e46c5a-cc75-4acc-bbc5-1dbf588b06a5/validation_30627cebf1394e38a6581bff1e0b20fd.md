### Title
`SwapAllowlistExtension` Gates Router Address Instead of End User — Any Non-Allowlisted User Bypasses Swap Restriction via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (required for any router-mediated swap to work on a restricted pool), every non-allowlisted user can bypass the per-user swap gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput` / `exactOutput`) is used, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol
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

The pool's `msg.sender` is now the **router address**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates an irreconcilable conflict:

| Router allowlisted? | Effect |
|---|---|
| No | All router-mediated swaps are blocked, even for legitimately allowlisted users |
| Yes | Every non-allowlisted user bypasses the per-user gate by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified market makers, whitelisted institutions, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker:

1. Calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool.
2. The router calls `pool.swap()`; the extension sees `sender = router`.
3. If the router is allowlisted, the swap proceeds regardless of the end user's identity.
4. The attacker extracts value from LP positions that were intended to trade only with vetted counterparties.

This is a direct loss of LP principal and a broken core pool invariant: the configured access policy is silently bypassed on every router-mediated swap.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical public swap entrypoint documented and deployed alongside the protocol.
- Any pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router address, which simultaneously opens the gate to all users.
- No special privilege, malicious setup, or non-standard token is required. Any EOA can call `exactInputSingle` on the public router.
- The existing test suite (`FullMetricExtension.t.sol`) only tests direct pool calls via `TestCaller`, never router-mediated swaps against an allowlisted pool, so the bypass is untested and undetected.

---

### Recommendation

The extension must recover the original end-user identity rather than the immediate `pool.swap()` caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to not forge the field, which is acceptable since the router is a known periphery contract.

2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant actor is often the recipient of output tokens. If the pool admin's intent is to gate who receives tokens, checking `recipient` (the second argument to `beforeSwap`) is correct and is unaffected by router intermediation.

3. **Dedicated router-aware allowlist**: Extend the extension to maintain a separate `trustedRouter` set; when `sender` is a trusted router, extract the real user from `extensionData` and check that address instead.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   (admin allowlists router for UX)
  allowedSwapper[pool][alice]  = true   (alice is a vetted user)
  allowedSwapper[pool][bob]    = false  (bob is NOT allowlisted)

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(bob_recipient, ...)
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; bob receives tokens from the restricted pool

Result:
  bob, a non-allowlisted user, successfully swaps on a pool
  that was configured to restrict trading to vetted counterparties.
  LP funds flow to an unauthorized party.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
