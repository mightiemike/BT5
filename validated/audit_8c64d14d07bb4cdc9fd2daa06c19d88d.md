### Title
`SwapAllowlistExtension` checks router address as swapper instead of actual user, allowing any unprivileged user to bypass a configured per-user allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (which is required for router-mediated swaps to function at all), every unprivileged user can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension hook.**

In `MetricOmmPool.swap()`, the `sender` forwarded to `_beforeSwap` is always `msg.sender` of the pool call:

```solidity
// MetricOmmPool.sol line 230-240
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

**Step 2 — Router calls `pool.swap()` directly; the user's address is never forwarded.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(recipient, ...)` with the router as `msg.sender`. There is no parameter in the pool's `swap()` signature for an originating user:

```solidity
// MetricOmmSimpleRouter.sol lines 71-80
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

The user's address (`msg.sender` of the router call) is stored only in transient callback context for payment settlement — it is never passed to the pool as the swap initiator.

**Step 3 — The allowlist extension checks the router address, not the actual user.**

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct), and `sender` is the address the pool passed — which is the router when the user goes through `MetricOmmSimpleRouter`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The dilemma this creates for the pool admin:**

| Admin choice | Effect |
|---|---|
| Do NOT allowlist the router | No user can swap through the router, even individually allowlisted ones |
| Allowlist the router | Every user — including explicitly disallowed ones — can bypass the allowlist via the router |

There is no configuration that simultaneously allows router-mediated swaps and enforces per-user restrictions. The guard is structurally inoperable for the router path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd users, whitelisted market makers, or protocol-controlled addresses) can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The disallowed user receives pool output tokens at oracle-anchored prices, draining LP value that was intended to be accessible only to allowlisted counterparties. The allowlist guard — the sole access-control mechanism on the swap path — is rendered ineffective for the public router entrypoint.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint documented and deployed for the protocol. Any user who discovers that the pool uses `SwapAllowlistExtension` can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. The bypass is reachable on every block as long as the router is allowlisted.

---

### Recommendation

The pool's `swap()` function should accept an explicit `originator` parameter (the actual user) that is forwarded to extension hooks as `sender`, separate from `msg.sender` (the caller/router). Alternatively, `SwapAllowlistExtension` should be redesigned to gate on `msg.sender` of the extension call (the pool) combined with an originator passed through `extensionData`, or the router should be prohibited from being allowlisted while per-user restrictions are active. The simplest fix is to add an `originator` field to the pool's `swap()` signature and forward it through `_beforeSwap` so extensions always see the economically responsible actor.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required for router-mediated swaps to work.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob receives pool output tokens despite being explicitly excluded from the allowlist.

The guard configured to protect LP funds from disallowed counterparties is silently bypassed on every router-mediated swap. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
