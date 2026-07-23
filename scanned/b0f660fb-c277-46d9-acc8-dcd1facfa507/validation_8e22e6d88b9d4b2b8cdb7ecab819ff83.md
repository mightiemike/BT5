### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the immediate caller of the pool, so `sender = router`. If the router is allowlisted (the only way to enable router-mediated swaps for legitimate users), every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that forwarded address against the per-pool allowlist:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter`, the call chain is:

```
User → MetricOmmSimpleRouter.exactInput*() → pool.swap(recipient=user, ...)
```

The pool sees `msg.sender = router`, so `sender = router` reaches the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for pool admins:

| Router allowlisted? | Effect |
|---|---|
| **No** | Allowlisted users cannot use the router at all — broken core functionality |
| **Yes** | Every non-allowlisted user can bypass the gate by routing through the router |

The analog to the SEDA gas-meter bug is exact: just as `MeterExecutorGasUniform` divides by the full `replicationFactor` instead of the effective count after removing outliers — causing the cap to be computed against the wrong denominator — `SwapAllowlistExtension` checks the wrong actor (the router, the intermediate) instead of the effective actor (the end user), causing the guard to be evaluated against the wrong identity.

---

### Impact Explanation

Any user can trade on a curated, allowlist-restricted pool by routing through the supported `MetricOmmSimpleRouter` periphery path, provided the pool admin has allowlisted the router (which is the only way to make the router usable for legitimate allowlisted users). This is a direct bypass of the core access-control invariant: **a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it**.

Severity: **High** — unauthorized swaps on a restricted pool constitute a direct policy and fund-flow violation; the pool's LP depositors accepted liquidity risk only for a curated counterparty set.

---

### Likelihood Explanation

Pool admins who deploy a `SwapAllowlistExtension` and also want their allowlisted users to use the router will naturally add the router to the allowlist. This is the expected operational path. The bypass is therefore reachable through normal, non-adversarial configuration and requires no privileged access from the attacker — only knowledge that the router is allowlisted.

---

### Recommendation

The extension must check the actual end user, not the immediate pool caller. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when the immediate `sender` is a known router.

2. **Recipient as the gated identity**: For swap allowlists, gate on `recipient` (the second parameter of `beforeSwap`) rather than `sender`, since `recipient` is always the economic beneficiary of the swap and is set to the end user even when routing through the periphery.

Option 2 is simpler and consistent with how `DepositAllowlistExtension` gates on `owner` (the economic LP) rather than `sender` (the adder contract).

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  Alice (allowlisted): allowedSwapper[pool][alice] = true
  Router (allowlisted to enable Alice's router swaps): allowedSwapper[pool][router] = true

Attack:
  Bob (not allowlisted) calls:
    router.exactInputSingle({ tokenIn, tokenOut, recipient: bob, ... })

  Router calls:
    pool.swap(recipient=bob, ...)          // msg.sender = router

  Pool calls:
    _beforeSwap(sender=router, ...)

  Extension evaluates:
    allowedSwapper[pool][router] == true   // ✓ passes

  Result: Bob's swap executes on the curated pool.
  Expected: revert NotAllowedToSwap.
``` [3](#0-2) [1](#0-0)

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
