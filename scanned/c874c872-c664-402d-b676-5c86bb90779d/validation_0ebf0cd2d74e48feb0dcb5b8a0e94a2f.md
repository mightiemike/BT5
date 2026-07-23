### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`, `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension including `SwapAllowlistExtension`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract address**, not the actual user. Because `SwapAllowlistExtension.beforeSwap` checks `sender` against the per-pool allowlist, the allowlist effectively gates the router's address rather than individual users. Any user can bypass a configured per-user swap allowlist by routing through the public `MetricOmmSimpleRouter`.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInput/exactOutput
         → MetricOmmPool.swap(recipient, ..., extensionData)
              msg.sender = MetricOmmSimpleRouter address
              → _beforeSwap(msg.sender=ROUTER, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=ROUTER, ...)
                        checks allowedSwapper[pool][ROUTER]
```

In `MetricOmmPool.swap`, the `sender` forwarded to every `beforeSwap` hook is unconditionally `msg.sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes this `sender` and dispatches it to every configured extension in order: [2](#0-1) 

The `SwapAllowlistExtension.beforeSwap` (described in the audit research target as an `allowAll/allowedSwapper` mapping keyed by `(pool, sender)`) therefore receives `sender = MetricOmmSimpleRouter` when the user enters through the router. The actual user's address is never presented to the guard. [3](#0-2) 

This creates an irresolvable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| Yes (`allowedSwapper[pool][router] = true`) | Every user bypasses the per-user allowlist by routing through the public router |
| No | Router-based swaps revert for **everyone**, including legitimately allowlisted users — breaking core swap functionality |

There is no mechanism in the pool or router for the router to forward the originating user's address as `sender`; the pool always uses `msg.sender`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd users, whitelisted market makers, or protocol-controlled accounts) is rendered ineffective. Any unprivileged user can execute swaps against the restricted pool by calling `MetricOmmSimpleRouter.exactInput` or `exactOutput`. This constitutes:

- **Broken core pool functionality**: the configured allowlist guard is bypassed on every router-mediated swap.
- **Potential direct fund impact**: unauthorized users can drain liquidity or extract value from a pool that was designed to be access-controlled, including pools whose LP positions are held by the protocol itself.

Severity: **Medium** (broken invariant with a reachable, unprivileged trigger; direct fund impact depends on pool configuration but the guard is structurally defeated).

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it.
- No special role, token balance, or admin action is required to route through it.
- The bypass is automatic: any swap submitted via the router presents the router's address to the allowlist, not the user's.
- Likelihood: **High**.

---

### Recommendation

The `SwapAllowlistExtension` must check the **originating user's identity**, not the intermediary router's address. Two approaches:

1. **Decode the actual user from `extensionData`**: The router encodes the originating `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires a coordinated change to `MetricOmmSimpleRouter` to always inject the caller's address.

2. **Check `recipient` instead of `sender`**: For swap allowlists intended to gate who receives output tokens, `recipient` is the economically relevant identity. However, `recipient` can also be set to an arbitrary address, so this only works if the intent is to gate output recipients.

3. **Expose a `trueSender` field in the pool's swap interface**: The pool accepts an explicit `trueSender` argument (validated against `msg.sender` or a trusted forwarder registry) and passes it to extensions instead of `msg.sender`.

The cleanest fix is option 1: `MetricOmmSimpleRouter` appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension` decodes and checks it when `sender` is a known router address.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension configured.
// Pool admin allowlists only `trustedUser` for swaps on this pool.
// allowedSwapper[pool][trustedUser] = true
// allowedSwapper[pool][router]      = false  (or not set)

// Scenario A: router NOT allowlisted → legitimate user cannot swap via router
vm.prank(trustedUser);
router.exactInput(...); // REVERTS — router address fails allowlist check

// Scenario B: pool admin adds router to allowlist to fix Scenario A
// allowedSwapper[pool][router] = true

// Now ANY user bypasses the allowlist:
vm.prank(bannedUser);
router.exactInput(pool, ...); 
// sender = router → allowedSwapper[pool][router] = true → PASSES
// bannedUser successfully swaps against the restricted pool
``` [4](#0-3) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
