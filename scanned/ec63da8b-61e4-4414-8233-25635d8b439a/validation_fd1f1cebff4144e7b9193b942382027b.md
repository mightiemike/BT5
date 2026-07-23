### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via the Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks whether the **router** is allowlisted, not the actual end user. If the pool admin adds the router to the allowlist (the only way to enable router-mediated swaps for legitimate users), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), i.e. the router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender == router when routed
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken UX |
| **Allowlist the router** | Every user, allowlisted or not, can bypass the guard by routing through the router |

There is no configuration that simultaneously allows router-mediated swaps for approved users and blocks unapproved users.

---

### Impact Explanation

Any user can trade on a curated, allowlist-restricted pool by calling `MetricOmmSimpleRouter` instead of calling the pool directly. The pool admin's curation policy is silently voided. Depending on the pool's purpose (e.g., institutional-only liquidity, KYC-gated pools, or pools with sensitive oracle configurations), this allows unauthorized price impact, fee extraction, and stop-loss or velocity-guard interactions by actors the admin explicitly intended to exclude. This is a direct admin-boundary break on a publicly reachable path.

---

### Likelihood Explanation

The exploit requires no special privileges, no malicious setup, and no non-standard tokens. Any user who can call the public router can trigger it. The router is a standard periphery contract intended for general use. The only precondition is that the pool admin has configured the `SwapAllowlistExtension` and has allowlisted the router (which is the only way to support router-mediated swaps for legitimate users).

---

### Recommendation

The extension must check the **economic actor** — the end user — not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData` for each hop, and the extension decodes and checks that address. The pool's `_beforeSwap` already forwards `extensionData` verbatim to extensions.
2. **Dedicated router allowlist slot**: Separate the "router is permitted to relay" check from the "end user is permitted to swap" check, requiring the router to attest the user identity in a verifiable way (e.g., signed payload or a trusted forwarder pattern).

Using `tx.origin` is not recommended as it breaks contract-wallet and multisig flows.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to allow any router-mediated swap.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` — alice is the only intended swapper.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)` → extension evaluates `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite never being allowlisted.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2)

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
