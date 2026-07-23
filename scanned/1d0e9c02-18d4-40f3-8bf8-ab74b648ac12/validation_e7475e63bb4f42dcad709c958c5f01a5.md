### Title
Router-Mediated Swap Bypasses Per-User `SwapAllowlistExtension` Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the user. Any user can therefore bypass a per-user allowlist on a curated pool by calling the public router instead of the pool directly.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook**

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

**Step 2 — Extension checks `allowedSwapper[pool][sender]`**

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

Here `msg.sender` = pool address (correct), and `sender` = whoever called `pool.swap()`.

**Step 3 — Router calls `pool.swap()` as itself**

`MetricOmmSimpleRouter.exactInputSingle` (and all other `exact*` entry points) calls:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
``` [4](#0-3) 

At the pool, `msg.sender` = **router address**. The hook therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The actor mismatch:**

| Flow | `sender` seen by hook | Intended gate |
|---|---|---|
| Direct `pool.swap()` | user's address | user's address ✓ |
| Router `exactInputSingle` | router's address | user's address ✗ |

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists specific user addresses intends to restrict swaps to those addresses. If the admin also allowlists the router (to support router-mediated swaps for those users), the allowlist is completely nullified: **any** address can call `MetricOmmSimpleRouter.exactInputSingle` and the hook will pass because it only checks whether the router is allowlisted. The per-user restriction is bypassed entirely.

Conversely, if the admin does not allowlist the router, allowlisted users cannot use the router at all, breaking expected UX and forcing direct pool interaction.

The primary exploit path is the bypass: an unprivileged user routes through the public router to trade on a pool that was supposed to be restricted to a curated set of addresses.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- Any user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` targeting any pool.
- No special setup is required beyond knowing the pool address.
- The bypass is deterministic and requires no race condition, oracle manipulation, or privileged access.

---

### Recommendation

The extension must gate on the **economic actor** (the end user), not the immediate caller of `pool.swap()`. Two options:

1. **Pass the original `msg.sender` through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension reads and verifies it. This requires a trust assumption that the router is honest, which must be enforced by also checking that `sender` (the router) is a known trusted router.

2. **Check both sender and a declared payer**: Require the extension to allowlist the router as a "trusted forwarder" and separately require the router to attest the real user in `extensionData`, with the extension verifying the attestation.

The simplest safe fix: if `sender` is a known trusted router, read the real user from `extensionData` and gate on that; otherwise gate on `sender` directly.

---

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(P, alice, true)  — only alice may swap.
3. Pool admin calls setAllowedToSwap(P, router, true) — to let alice use the router.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...}).
5. Router calls P.swap(...) — msg.sender at pool = router.
6. beforeSwap receives sender = router.
7. allowedSwapper[P][router] == true → hook passes.
8. Bob's swap executes on the restricted pool. ✓ bypass confirmed.
```

Assert: `allowedSwapper[pool][router] == true` does not imply `allowedSwapper[pool][bob] == true`, yet Bob's swap succeeds.

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
