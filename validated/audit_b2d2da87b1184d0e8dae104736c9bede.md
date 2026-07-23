### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual swapper, allowing any user to bypass the per-user allowlist via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension` is described as "Gates `swap` by swapper address, per pool." Its `beforeSwap` hook checks `allowedSwapper[msg.sender][sender]`, where `sender` is the first parameter forwarded by the pool — which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to work for allowlisted users), every unprivileged user can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← direct caller; the router when routed
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this unchanged as the first positional argument to every configured extension:

```solidity
// ExtensionCalling.sol lines 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender = router
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol lines 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check passes whenever `allowedSwapper[pool][router]` is `true`. The actual user (`recipient`) is never inspected.

The pool admin must allowlist the router to permit any router-mediated swap for legitimate users. Once the router is allowlisted, the per-user mapping `allowedSwapper[pool][user]` is completely bypassed for every user who routes through the router — the allowlist is rendered a no-op for that entry path.

This is the direct analog to the empty `RegisterLegacyAminoCodec`: the guard is wired and appears active, but the identity it checks (`sender` = router) is not the identity the policy was meant to restrict (`recipient` = actual user), so the configured protection is silently absent for the router path.

---

### Impact Explanation

Any user who is explicitly **not** on the allowlist can swap on a curated pool by routing through `MetricOmmSimpleRouter`. The pool admin's access-control boundary — the primary mechanism for restricting who may trade — is bypassed by an unprivileged, publicly available path. Curated pools designed for KYC'd counterparties, institutional-only flows, or pools with favorable fee terms for specific market makers are fully open to unauthorized traders. This is an admin-boundary break: an unprivileged path circumvents a configured pool-admin cap.

---

### Likelihood Explanation

- The router is a standard, publicly deployed periphery contract.
- Any pool admin who wants allowlisted users to be able to use the router **must** add the router to the allowlist; there is no other mechanism.
- Once the router is allowlisted (the expected production configuration), the bypass requires zero special privileges: any address calls the router, which calls the pool, and the extension passes.
- The attacker can verify the configuration entirely on-chain before acting.

---

### Recommendation

Gate on the economically relevant actor. Two options:

1. **Check `recipient` instead of `sender`**: The output-token receiver (`recipient`) is the user who benefits from the swap. Changing the check to `allowedSwapper[msg.sender][recipient]` closes the router bypass for the common case where the router sets `recipient = user`.

2. **Require the router to attest the real user via `extensionData`**: The extension decodes the actual user address from `extensionData` and checks that address. This is more robust but requires router cooperation.

Additionally, add the `onlyPool` modifier to `SwapAllowlistExtension.beforeSwap` (it is present in the base class but dropped by the override), so the extension cannot be called directly by arbitrary addresses.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, user1, true)
   → allowedSwapper[pool][user1] = true
3. Pool admin calls setAllowedToSwap(pool, router, true)
   → allowedSwapper[pool][router] = true
   (required so user1 can swap via the router)

Attack
──────
4. attacker (not in allowlist) calls:
       router.exactInput(pool, ..., recipient=attacker)
5. Router calls:
       pool.swap(recipient=attacker, ...)   // msg.sender = router
6. Pool calls _beforeSwap(sender=router, recipient=attacker, ...)
7. Extension evaluates:
       allowedSwapper[pool][router]  →  true  ✓  (passes)
8. Swap executes; attacker receives output tokens.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
