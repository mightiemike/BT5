### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook receives `sender` as the identity to gate. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap()` function passes `msg.sender` — the router contract — as `sender` to the extension. If the pool admin allowlists the router address (the only way to permit router-mediated swaps for legitimate users), the allowlist check degenerates: every user who calls the public router passes the gate regardless of whether they were individually authorized.

---

### Finding Description

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// MetricOmmPool.sol ~L230
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
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

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim to every configured extension:

```solidity
// ExtensionCalling.sol ~L160
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`. When the user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the user. The pool admin faces an impossible choice:

- **Do not allowlist the router** → legitimate allowlisted users cannot use the router at all.
- **Allowlist the router** → every user on the network can bypass the allowlist by calling the public router, because `sender = router` passes the check unconditionally.

The second branch is the exploitable path. The router is a public, permissionless contract. Once it is allowlisted, the allowlist provides zero protection.

This is the direct analog to the external double-claiming bug: just as `position.fromEpoch - 1` caused the UnwindingModule to count a user from the wrong boundary (granting them rewards they already received), the `sender = router` substitution causes the allowlist to check the wrong identity — the intermediary contract rather than the economic actor — granting swap access to users who were never individually authorized.

---

### Impact Explanation

A permissioned pool using `SwapAllowlistExtension` is intended to restrict swaps to a curated set of counterparties (e.g., KYC'd addresses, protocol-owned contracts, or whitelisted market makers). Once the router is allowlisted to support legitimate users, the restriction is fully nullified. Any unprivileged user can:

1. Call `MetricOmmSimpleRouter.exactInput` / `exactOutput` targeting the restricted pool.
2. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
3. `SwapAllowlistExtension.beforeSwap` sees `sender = router`, which is allowlisted → passes.
4. The swap executes at oracle-derived prices against LP liquidity that was deposited under the assumption of a restricted counterparty set.

Impact: unauthorized traders drain LP value at oracle prices, LPs suffer unexpected impermanent loss or adverse selection, and the pool's core access-control invariant is broken. This is a direct loss of LP principal above contest thresholds.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, deployed periphery contract — no special role or privilege is required to call it.
- Any pool that enables the `SwapAllowlistExtension` and also wants to support router-mediated swaps for legitimate users must allowlist the router, triggering the vulnerability automatically.
- The attacker needs no special setup: one call to the router suffices.
- The only mitigation that avoids the bug is forcing all allowlisted users to call the pool directly, which defeats the purpose of the router and is not enforced anywhere.

---

### Recommendation

The `beforeSwap` hook should gate the **originating user**, not the immediate `msg.sender` of the pool. Two options:

1. **Pass the true user through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData`, and have `SwapAllowlistExtension` decode and check that address instead of `sender`.
2. **Check `recipient` as a proxy for the user** (only valid if the pool enforces `recipient == caller` at the router level, which it currently does not).
3. **Require direct pool interaction** for allowlisted pools and document that the router is incompatible with `SwapAllowlistExtension` — but this is a design restriction, not a fix.

Option 1 is the cleanest: the extension should check `allowedSwapper[pool][trueUser]` where `trueUser` is recovered from `extensionData` when the call originates from a trusted router, or falls back to `sender` for direct calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap order.
  - Pool admin calls allowedSwapper[pool][router] = true  (to enable router for legitimate users).
  - Pool admin does NOT allowlist attacker address.

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInput(pool, zeroForOne=true, amount, ...)
  2. Router calls pool.swap(recipient=attacker, zeroForOne, amount, priceLimit, callbackData, extensionData)
  3. pool._beforeSwap(msg.sender=router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  4. allowedSwapper[pool][router] == true → hook returns selector, no revert.
  5. Swap executes. Attacker receives token1 at oracle price.

Expected: revert NotAllowedToSwap() because attacker is not individually allowlisted.
Actual:   swap succeeds because the router — not the attacker — is the checked identity.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

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
