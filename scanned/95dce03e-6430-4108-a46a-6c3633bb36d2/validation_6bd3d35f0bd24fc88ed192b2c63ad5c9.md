### Title
SwapAllowlistExtension Gates Router Address Instead of End-User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. This creates an irreconcilable inconsistency: either the router must be allowlisted (enabling any user to bypass the allowlist), or the router is not allowlisted (blocking all allowlisted users from using the router). There is no configuration that correctly gates individual end-users who route through the periphery.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (used as the mapping key), and `sender` is the direct caller of `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactIn*` or `exactOut*`, the router calls `pool.swap()`, making `sender = router`, not the end user.

The allowlist check therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This produces two broken states:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Any unprivileged user bypasses the allowlist by routing through the router |
| Router **is not** allowlisted | All allowlisted users are blocked from using the router entirely |

Neither state correctly enforces per-user access control for router-mediated swaps.

The analog to the wfcash bug is exact: just as `_previewDeposit` used a different code path than `_mintInternal` for an edge case (producing wrong share amounts), `SwapAllowlistExtension` uses a different effective identity check depending on the entry path (direct vs. router), making the guard inconsistent with its intended semantics.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses can be bypassed by any unprivileged user simply by calling `MetricOmmSimpleRouter` instead of `MetricOmmPool.swap` directly, provided the router is allowlisted. Since the router is a public periphery contract and pool admins are expected to allowlist it to enable normal router-mediated trading for their permitted users, this is a reachable production condition. Non-allowlisted users gain full swap access to a pool that was explicitly configured to exclude them, directly violating the pool's access-control invariant and potentially exposing LP funds to trades from untrusted counterparties.

---

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router (a routine operational step for any pool that wants to support router-mediated swaps for its permitted users). No privileged attacker action, malicious setup, or non-standard token is needed. Any unprivileged address can exploit this by calling the public router with a valid swap path targeting the restricted pool. The condition is reachable in normal production operation.

---

### Recommendation

The extension must gate the economic actor, not the intermediary. Two sound approaches:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should accept an explicit `swapper` parameter and forward it in `extensionData`. `SwapAllowlistExtension.beforeSwap` should decode and check this value when present, falling back to `sender` for direct calls.

2. **Check `recipient` instead of `sender`**: If the pool's swap semantics guarantee that `recipient` is always the end-user's address (not the router), the allowlist can gate `recipient`. This requires verifying that the router always sets `recipient` to the originating user, not to itself.

Either fix must be applied consistently so that the identity checked by the extension is the same identity the pool admin intended to gate, regardless of whether the swap arrives directly or through the router.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as `extension1` with `beforeSwap` order set.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInSingle(pool, ...)`.
5. Router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool that was configured to exclude him, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2)

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
