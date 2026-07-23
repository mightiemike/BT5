### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks the router's address against the allowlist instead of the actual user's address. If the router is allowlisted — a natural configuration for any pool that wants allowlisted users to be able to use the router — every unpermissioned user can bypass the gate by routing through the same public contract.

---

### Finding Description

**Root cause — wrong actor bound in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool.

**How the pool binds `sender`:**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value verbatim into the call to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

**How the router breaks the identity chain:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` seen by the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The same pattern holds for `exactInput` (all hops call `pool.swap` from the router) and for intermediate hops in `exactOutput` (called from inside the callback, where `msg.sender` is the previous pool, not the user). [5](#0-4) 

**The dilemma this creates for pool admins:**

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | ❌ blocked | ❌ blocked |
| Yes | ✅ passes | ✅ passes — **bypass** |

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Doing so silently opens the gate to every user on the network, because the extension has no way to distinguish which EOA initiated the router call.

---

### Impact Explanation

Any unpermissioned user can swap on a pool that is configured to be restricted to specific counterparties by routing through `MetricOmmSimpleRouter`. Consequences include:

- **Direct LP fund loss**: the restricted pool may be priced for a curated set of market participants; unauthorized traders can extract value from LPs through the oracle-derived bid/ask spread.
- **Compliance/curation failure**: pools deployed for KYC'd or whitelisted counterparties are fully open to the public.
- **Broken core pool functionality**: the allowlist extension, which is the pool's primary access-control mechanism, provides no protection on the router path.

---

### Likelihood Explanation

Medium. The trigger condition — the router being allowlisted — is a natural and expected configuration. Any pool admin who wants allowlisted users to be able to use the standard periphery router must allowlist it. The router is a public, permissionless contract, so once it is allowlisted the bypass is available to every address on-chain with no further preconditions.

---

### Recommendation

The extension must gate on the economically relevant actor, not the intermediary. Two sound approaches:

1. **Pass the originating user through `extensionData`**: the router encodes `msg.sender` into the extension payload; the `SwapAllowlistExtension` decodes and checks it. The pool admin allowlists individual users, not the router.
2. **Check `sender` only when it is not a known router**: the extension maintains a registry of trusted routers and, when `sender` is a router, falls back to a user identity embedded in `extensionData`.

The current architecture where `sender = msg.sender of pool.swap()` is structurally incompatible with a per-user allowlist on any pool that also permits router-mediated swaps.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist a legitimate user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist the router so userA can use it

Attack
──────
4. userB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: userB, ...})

5. Router executes:
       pool.swap(userB, zeroForOne, amount, priceLimit, "", extensionData)
       // msg.sender to pool = router

6. Pool executes:
       _beforeSwap(msg.sender=router, ...)

7. ExtensionCalling encodes sender=router and calls:
       extension.beforeSwap(sender=router, ...)

8. SwapAllowlistExtension checks:
       allowedSwapper[pool][router]  →  true  →  no revert

9. Swap executes. userB has bypassed the allowlist and swapped on the restricted pool.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
